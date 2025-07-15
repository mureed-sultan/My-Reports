from odoo import models, fields, api
from datetime import date
import io
import csv
from odoo import http
from odoo.http import request, content_disposition


class POSSalesReport(models.Model):
    _name = 'pos.sales.report'
    _description = 'POS Sales Report'

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
    csv_file = fields.Binary("CSV File", readonly=True)
    csv_filename = fields.Char("Filename", readonly=True)

    def action_fetch_report(self):
        self.ensure_one()
        rows = self.fetch_report_data()
        self.html_table = self._build_html_table(rows)

    def fetch_report_data(self):
        self.ensure_one()
        query = """
            SELECT 
                po.id AS order_id,
                po.name AS order_reference,
                po.date_order::date AS order_date,
                rp.name AS customer_name,
                pc.name AS pos_config_name,
                ps.name AS session_name,
                ru.login AS cashier_login,
                he.name AS employee_name,
                pl.name AS pricelist_name,
                pt.name AS product_name,
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

        query += " ORDER BY po.date_order DESC, po.name, pol.id "

        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()

        for r in rows:
            r['tax_value'] = (r['line_total_incl'] or 0) - (r['subtotal'] or 0)

        return rows

    def _build_html_table(self, rows):
        if not rows:
            return "<p>No data found. Adjust filters and try again.</p>"

        total_qty = sum(r['quantity'] or 0 for r in rows)
        total_subtotal = sum(r['subtotal'] or 0 for r in rows)
        total_tax = sum(r['tax_value'] or 0 for r in rows)
        total_total_incl = sum(r['line_total_incl'] or 0 for r in rows)

        totals_block = f"""
            <h4>Summary Totals</h4>
            <ul>
                <li>Total Quantity: <b>{total_qty:.2f}</b></li>
                <li>Total Subtotal (Before Tax): <b>{total_subtotal:.2f}</b></li>
                <li>Total Tax: <b>{total_tax:.2f}</b></li>
                <li>Total Including Tax: <b>{total_total_incl:.2f}</b></li>
            </ul>
            <br/>
        """

        table = """
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
                        <th>Line Total Incl</th>
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
                    <td>{total_total_incl:.2f}</td>
                    <td></td>
                </tr>
        """.format(
            total_qty=total_qty,
            total_subtotal=total_subtotal,
            total_tax=total_tax,
            total_total_incl=total_total_incl
        )

        for r in rows:
            pricelist_name = r.get('pricelist_name', '')
            if isinstance(pricelist_name, dict):
                pricelist_name = pricelist_name.get('en_US', '')

            product_name = r.get('product_name', '')
            if isinstance(product_name, dict):
                product_name = product_name.get('en_US', '')

            product_display = "{} ({:.2f} x {:.2f} = {:.2f})".format(
                product_name,
                r['quantity'] or 0,
                r['price_unit'] or 0,
                (r['quantity'] or 0) * (r['price_unit'] or 0)
            )

            table += f"""
                <tr>
                    <td><a href="/web#id={r['order_id']}&model=pos.order&view_type=form" target="_blank">{r['order_reference']}</a></td>
                    <td>{r['order_date']}</td>
                    <td>{r['customer_name'] or ''}</td>
                    <td>{r['pos_config_name'] or ''}</td>
                    <td>{r['session_name'] or ''}</td>
                    <td>{r['cashier_login'] or ''}</td>
                    <td>{r['employee_name'] or ''}</td>
                    <td>{pricelist_name}</td>
                    <td>{product_display}</td>
                    <td>{r['quantity'] or 0:.2f}</td>
                    <td>{r['original_price'] or 0:.2f}</td>
                    <td>{r['price_unit'] or 0:.2f}</td>
                    <td>{r['subtotal'] or 0:.2f}</td>
                    <td>{r['tax_value'] or 0:.2f}</td>
                    <td>{r['line_total_incl'] or 0:.2f}</td>
                    <td>{r['order_total'] or 0:.2f}</td>
                </tr>
            """

        table += "</tbody></table>"
        return totals_block + table

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
            'Unit Price', 'Subtotal', 'Tax', 'Line Total Incl', 'Order Total'
        ])

        for r in rows:
            pricelist_name = r.get('pricelist_name', '')
            if isinstance(pricelist_name, dict):
                pricelist_name = pricelist_name.get('en_US', '')

            product_name = r.get('product_name', '')
            if isinstance(product_name, dict):
                product_name = product_name.get('en_US', '')

            product_display = "{} ({:.2f} x {:.2f} = {:.2f})".format(
                product_name,
                r['quantity'] or 0,
                r['price_unit'] or 0,
                (r['quantity'] or 0) * (r['price_unit'] or 0)
            )

            writer.writerow([
                r['order_reference'],
                r['order_date'],
                r.get('customer_name', ''),
                r.get('pos_config_name', ''),
                r.get('session_name', ''),
                r.get('cashier_login', ''),
                r.get('employee_name', ''),
                pricelist_name,
                product_display,
                f"{r.get('quantity', 0):.2f}",
                f"{r.get('original_price', 0):.2f}",
                f"{r.get('price_unit', 0):.2f}",
                f"{r.get('subtotal', 0):.2f}",
                f"{r.get('tax_value', 0):.2f}",
                f"{r.get('line_total_incl', 0):.2f}",
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
