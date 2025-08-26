from odoo import models, fields, api, http
from datetime import date, datetime
import io
import csv
import json
from odoo.http import request, content_disposition


class POSSalesReport(models.Model):
    _name = 'pos.sales.report'
    _description = 'POS Sales Report'

    # ========= FILTER FIELDS =========
    start_date = fields.Date("Start Date", default=lambda self: date.today())
    end_date = fields.Date("End Date", default=lambda self: date.today())
    product_ids = fields.Many2many('product.product', string="Products")
    category_ids = fields.Many2many('product.category', string="Product Categories")
    branch_ids = fields.Many2many('pos.config', string="POS Branches")
    user_ids = fields.Many2many('res.users', string="Cashiers")
    session_ids = fields.Many2many('pos.session', string="Sessions")
    pricelist_ids = fields.Many2many('product.pricelist', string="Pricelists")
    state = fields.Selection([
        ('draft', 'New'),
        ('paid', 'Paid'),
        ('invoiced', 'Invoiced'),
        ('done', 'Done'),
        ('cancel', 'Cancelled')
    ], string="Order Status")
    html_table = fields.Html("Report", sanitize=False)
    report_data_json = fields.Json("Report Data")  # <-- store rows as JSON

    # ========= MAIN ACTION =========
    def action_fetch_report(self):
        self.ensure_one()
        rows = self.fetch_report_data()

        # convert dates to strings for JSON safety
        def serialize(value):
            if isinstance(value, (date, datetime)):
                return value.isoformat()
            return value

        serialized_rows = [
            {k: serialize(v) for k, v in row.items()} for row in rows
        ]

        # save html + json
        self.html_table = self._build_html_table(rows)
        self.report_data_json = serialized_rows

    # ========= DATA FETCHING =========
    def fetch_report_data(self):
        self.ensure_one()

        query = """
            SELECT 
                pol.id AS line_id,
                po.id AS order_id,
                po.name AS order_reference,
                po.date_order::date AS order_date,
                rp.name AS customer_name,
                pc.name AS pos_config_name,
                ps.name AS session_name,
                ru.login AS cashier_login,
                he.name AS employee_name,
                pl.name ->> 'en_US' AS pricelist_name,
                pt.name ->> 'en_US' AS product_name,
                pt.type AS product_type,
                pol.qty AS quantity,
                pt.list_price AS original_price,
                pol.price_unit AS price_unit,
                pol.price_subtotal AS subtotal,
                pol.price_subtotal_incl AS line_total_incl,
                po.amount_total AS order_total
            FROM 
                pos_order_line pol
            JOIN pos_order po ON pol.order_id = po.id
            LEFT JOIN res_partner rp ON po.partner_id = rp.id
            LEFT JOIN pos_session ps ON po.session_id = ps.id
            LEFT JOIN pos_config pc ON po.config_id = pc.id
            LEFT JOIN res_users ru ON po.user_id = ru.id
            LEFT JOIN product_pricelist pl ON po.pricelist_id = pl.id
            JOIN product_product pp ON pol.product_id = pp.id
            JOIN product_template pt ON pp.product_tmpl_id = pt.id
            LEFT JOIN hr_employee he ON pol.note = he.barcode
            WHERE TRUE
        """
        params = []

        # ==== APPLY FILTERS ====
        if self.start_date:
            query += " AND po.date_order::date >= %s"
            params.append(self.start_date)
        if self.end_date:
            query += " AND po.date_order::date <= %s"
            params.append(self.end_date)
        if self.branch_ids:
            query += " AND po.config_id = ANY(%s)"
            params.append([b.id for b in self.branch_ids])
        if self.product_ids:
            query += " AND pol.product_id = ANY(%s)"
            params.append([p.id for p in self.product_ids])
        if self.category_ids:
            query += " AND pt.categ_id = ANY(%s)"
            params.append([c.id for c in self.category_ids])
        if self.user_ids:
            query += " AND po.user_id = ANY(%s)"
            params.append([u.id for u in self.user_ids])
        if self.session_ids:
            query += " AND po.session_id = ANY(%s)"
            params.append([s.id for s in self.session_ids])
        if self.pricelist_ids:
            query += " AND po.pricelist_id = ANY(%s)"
            params.append([pl.id for pl in self.pricelist_ids])
        if self.state:
            query += " AND po.state = %s"
            params.append(self.state)

        # ==== ORDERING ====
        query += " ORDER BY po.date_order DESC, po.id DESC, pol.id ASC"

        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()

        # ==== ENRICH ROWS ====
        for r in rows:
            qty = float(r['quantity'] or 0.0)
            original = float(r['original_price'] or 0.0)  # pt.list_price
            unit_price = float(r['price_unit'] or 0.0)    # actual applied price

            # Pricelist discount calculation
            discount_value = (original - unit_price) * qty if original > unit_price else 0.0
            discount_percent = ((original - unit_price) / original * 100.0) if original > 0 else 0.0

            r['tax_value'] = (r['line_total_incl'] or 0) - (r['subtotal'] or 0)
            r['discount_amount'] = discount_value
            r['discount'] = discount_percent
            r['net_sale'] = r['subtotal'] or 0.0
            r['sale_after_tax'] = r['line_total_incl'] or 0.0
            r['product_display'] = f"{r['product_name']} ({qty:.2f} x {unit_price:.2f} = {r['subtotal']:.2f})"

        return rows

    # ========= HTML TABLE BUILDER =========
    def _build_html_table(self, rows):
        if not rows:
            return "<p>No data found. Adjust filters and try again.</p>"

        # Totals
        total_qty = sum(r['quantity'] or 0 for r in rows)
        total_subtotal = sum(r['subtotal'] or 0 for r in rows)
        total_tax = sum(r['tax_value'] or 0 for r in rows)
        total_total_incl = sum(r['line_total_incl'] or 0 for r in rows)
        total_discount_amount = sum(r.get('discount_amount', 0) for r in rows)

        totals_block = f"""
            <h4>Summary Totals</h4>
            <ul>
                <li>Total Quantity: <b>{total_qty:.2f}</b></li>
                <li>Total Subtotal (Before Tax): <b>{total_subtotal:.2f}</b></li>
                <li>Total Tax: <b>{total_tax:.2f}</b></li>
                <li>Total Discount: <b>{total_discount_amount:.2f}</b></li>
                <li>Total Including Tax: <b>{total_total_incl:.2f}</b></li>
            </ul>
            <br/>
        """

        table = f"""
            <table class='table table-sm table-bordered'>
                <thead>
                    <tr>
                        <th>Order Ref</th>
                        <th>Order Date</th>
                        <th>Customer</th>
                        <th>POS</th>
                        <th>Session</th>
                        <th>Cashier</th>
                        <th>Employee</th>
                        <th>Pricelist</th>
                        <th>Product</th>
                        <th>Qty</th>
                        <th>Original Price</th>
                        <th>Unit Price</th>
                        <th>Subtotal</th>
                        <th>Tax</th>
                        <th>Discount (%)</th>
                        <th>Discount (Value)</th>
                        <th>Net Sale Before Tax</th>
                        <th>Sale After Tax</th>
                        <th>Order Total</th>
                    </tr>
                </thead>
                <tbody>
                <tr style="font-weight:bold; background:#f0f0f0;">
                    <td colspan="9" style="text-align:right;">TOTALS:</td>
                    <td>{total_qty:.2f}</td>
                    <td></td>
                    <td></td>
                    <td>{total_subtotal:.2f}</td>
                    <td>{total_tax:.2f}</td>
                    <td></td>
                    <td>{total_discount_amount:.2f}</td>
                    <td>{total_subtotal:.2f}</td>
                    <td>{total_total_incl:.2f}</td>
                    <td></td>
                </tr>
        """

        # Render every order line separately
        for r in rows:
            table += f"""
                <tr data-line-id="{r['line_id']}">
                    <td><a href="/web#id={r['order_id']}&model=pos.order&view_type=form" target="_blank">{r['order_reference']}</a></td>
                    <td>{r['order_date']}</td>
                    <td>{r['customer_name'] or ''}</td>
                    <td>{r['pos_config_name'] or ''}</td>
                    <td>{r['session_name'] or ''}</td>
                    <td>{r['cashier_login'] or ''}</td>
                    <td>{r['employee_name'] or ''}</td>
                    <td>{r['pricelist_name'] or ''}</td>
                    <td>{r['product_display']}</td>
                    <td>{r['quantity'] or 0:.2f}</td>
                    <td>{r['original_price'] or 0:.2f}</td>
                    <td>{r['price_unit'] or 0:.2f}</td>
                    <td>{r['subtotal'] or 0:.2f}</td>
                    <td>{r['tax_value'] or 0:.2f}</td>
                    <td>{r['discount'] or 0:.2f}</td>
                    <td>{r['discount_amount'] or 0:.2f}</td>
                    <td>{r['net_sale'] or 0:.2f}</td>
                    <td>{r['sale_after_tax'] or 0:.2f}</td>
                    <td>{r['order_total'] or 0:.2f}</td>
                </tr>
            """
        table += "</tbody></table>"
        return totals_block + table

    # ========= CSV EXPORT =========
    def action_generate_csv(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': f'/pos_sales_report/download/{self.id}',
            'target': 'self',
        }


class POSReportController(http.Controller):

    @http.route('/pos_sales_report/download/<int:report_id>', type='http', auth='user')
    def download_csv(self, report_id, **kwargs):
        report = request.env['pos.sales.report'].browse(report_id).sudo()
        rows = report.fetch_report_data()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Order Ref', 'Order Date', 'Customer', 'POS', 'Session', 'Cashier',
            'Employee', 'Pricelist', 'Product', 'Qty', 'Original Price',
            'Unit Price', 'Subtotal', 'Tax', 'Discount (%)', 'Discount (Value)',
            'Net Sale Before Tax', 'Sale After Tax', 'Order Total'
        ])

        for r in rows:
            writer.writerow([
                r['order_reference'],
                r['order_date'].isoformat() if isinstance(r['order_date'], (date, datetime)) else r['order_date'],
                r.get('customer_name', ''),
                r.get('pos_config_name', ''),
                r.get('session_name', ''),
                r.get('cashier_login', ''),
                r.get('employee_name', ''),
                r.get('pricelist_name', ''),
                r.get('product_display', ''),
                f"{r.get('quantity', 0):.2f}",
                f"{r.get('original_price', 0):.2f}",
                f"{r.get('price_unit', 0):.2f}",
                f"{r.get('subtotal', 0):.2f}",
                f"{r.get('tax_value', 0):.2f}",
                f"{r.get('discount', 0):.2f}",
                f"{r.get('discount_amount', 0):.2f}",
                f"{r.get('net_sale', 0):.2f}",
                f"{r.get('sale_after_tax', 0):.2f}",
                f"{r.get('order_total', 0):.2f}",
            ])

        csv_data = output.getvalue().encode('utf-8')
        output.close()

        filename = f"pos_sales_report_{report_id}.csv"
        headers = [
            ('Content-Type', 'text/csv'),
            ('Content-Disposition', content_disposition(filename)),
        ]
        return request.make_response(csv_data, headers)
