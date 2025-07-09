# pos_report.py

from odoo import models, fields, api
import re
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
                string_agg(DISTINCT he.name, ', ') AS employee_name,
                pl.name AS pricelist_name,
                json_agg(json_build_object(
                    'product', pt.name,
                    'price_unit', pol.price_unit,
                    'qty', pol.qty,
                    'original_price_unit', pt.list_price
                )) AS products_json,
                SUM(pol.qty) AS total_qty,
                SUM(pt.list_price * pol.qty) AS total_before_tax_discount,
                SUM(pol.price_subtotal) AS total_subtotal,
                SUM(pol.price_subtotal_incl) AS total_incl,
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

        query += """
            GROUP BY po.id, po.name, po.date_order::date, rp.name, pc.name, ps.name,
                     ru.login, pl.name, po.amount_total
            ORDER BY po.date_order DESC
        """

        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()

        for r in rows:
            discount_total = 0.0
            products_json = r.get('products_json') or []
            for p in products_json:
                original = p.get('original_price_unit') or 0.0
                discounted = p.get('price_unit') or 0.0
                qty = p.get('qty') or 0.0
                line_discount = (original - discounted) * qty
                if line_discount > 0:
                    discount_total += line_discount

            r['discount_value'] = round(discount_total, 2)
            r['subtotal_before_tax'] = r['total_subtotal'] or 0
            r['tax_value'] = (r['total_incl'] or 0) - (r['total_subtotal'] or 0)

        return rows

    def _extract_discount_percent(self, pricelist_name):
        if isinstance(pricelist_name, dict):
            pricelist_name = pricelist_name.get('en_US') or list(pricelist_name.values())[0] or ''
        elif not isinstance(pricelist_name, str):
            pricelist_name = str(pricelist_name or '')
        m = re.search(r'(\d+)%', pricelist_name)
        return float(m.group(1)) if m else 0.0

    def _get_pricelist_display(self, pricelist_name):
        if isinstance(pricelist_name, dict):
            return pricelist_name.get('en_US') or list(pricelist_name.values())[0] or ''
        elif pricelist_name:
            return str(pricelist_name)
        return ''

    def _build_html_table(self, rows):
        if not rows:
            return "<p>No data found. Adjust filters and try again.</p>"

        # Totals
        total_qty = sum(r['total_qty'] or 0 for r in rows)
        total_before_tax_discount = sum(r['total_before_tax_discount'] or 0 for r in rows)
        total_subtotal = sum(r['total_subtotal'] or 0 for r in rows)
        total_discount = sum(r['discount_value'] or 0 for r in rows)
        total_tax = sum(r['tax_value'] or 0 for r in rows)
        total_order = sum(r['order_total'] or 0 for r in rows)

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
                        <th>Products</th>
                        <th>Total Qty</th>
                        <th>Total Before Tax & Discount</th>
                        <th>Subtotal Before Tax</th>
                        <th>Discount Value</th>
                        <th>Tax Value</th>
                        <th>Order Total</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="font-weight:bold; background:#f0f0f0;">
                        <td colspan="9" style="text-align:right;">TOTALS:</td>
                        <td>{total_qty:.2f}</td>
                        <td>{total_before_tax_discount:.2f}</td>
                        <td>{total_subtotal:.2f}</td>
                        <td>{total_discount:.2f}</td>
                        <td>{total_tax:.2f}</td>
                        <td>{total_order:.2f}</td>
                    </tr>
        """.format(
            total_qty=total_qty,
            total_before_tax_discount=total_before_tax_discount,
            total_subtotal=total_subtotal,
            total_discount=total_discount,
            total_tax=total_tax,
            total_order=total_order,
        )

        for r in rows:
            pricelist_name = self._get_pricelist_display(r['pricelist_name'])
            product_lines = []
            for p in r['products_json']:
                prod_name = p['product']
                if isinstance(prod_name, dict):
                    prod_name = prod_name.get('en_US') or list(prod_name.values())[0] or ''
                line = f"{prod_name} ({p['original_price_unit']:.2f}→{p['price_unit']:.2f}x{p['qty']:.2f})"
                product_lines.append(line)
            product_html = ", ".join(product_lines)
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
                    <td style="white-space: pre-wrap;">{product_html}</td>
                    <td>{r['total_qty'] or 0:.2f}</td>
                    <td>{r['total_before_tax_discount'] or 0:.2f}</td>
                    <td>{r['total_subtotal'] or 0:.2f}</td>
                    <td>{r['discount_value'] or 0:.2f}</td>
                    <td>{r['tax_value'] or 0:.2f}</td>
                    <td>{r['order_total'] or 0:.2f}</td>
                </tr>
            """

        table += "</tbody></table>"
        return table

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
            'Employee', 'Pricelist', 'Products', 'Total Qty',
            'Total Before Tax & Discount', 'Subtotal Before Tax',
            'Discount Value', 'Tax Value', 'Order Total'
        ])

        for r in rows:
            product_lines = []
            for p in r['products_json']:
                name = p['product']
                if isinstance(name, dict):
                    name = name.get('en_US') or list(name.values())[0] or ''
                line = f"{name} ({p['original_price_unit']:.2f}->{p['price_unit']:.2f}x{p['qty']:.2f})"
                product_lines.append(line)

            writer.writerow([
                r['order_reference'],
                r['order_date'],
                r.get('customer_name', ''),
                r.get('pos_config_name', ''),
                r.get('session_name', ''),
                r.get('cashier_login', ''),
                r.get('employee_name', ''),
                r.get('pricelist_name', ''),
                " | ".join(product_lines),
                f"{r.get('total_qty', 0):.2f}",
                f"{r.get('total_before_tax_discount', 0):.2f}",
                f"{r.get('subtotal_before_tax', 0):.2f}",
                f"{r.get('discount_value', 0):.2f}",
                f"{r.get('tax_value', 0):.2f}",
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
