from odoo import models, fields, api
import io
import base64
import csv
from datetime import date


class POSCustomerReport(models.TransientModel):
    _name = "pos.customer.report"
    _description = "POS Customer Report Wizard"

    # ==== Filters ====
    start_date = fields.Date("Start Date", default=lambda self: date.today())
    end_date = fields.Date("End Date", default=lambda self: date.today())
    branch_ids = fields.Many2many("pos.config", string="Branches")
    session_ids = fields.Many2many("pos.session", string="Sessions")
    user_ids = fields.Many2many("res.users", string="Cashiers")
    product_ids = fields.Many2many("product.product", string="Products")
    category_ids = fields.Many2many("product.category", string="Categories")
    pricelist_ids = fields.Many2many("product.pricelist", string="Pricelists")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('paid', 'Paid'),
        ('invoiced', 'Invoiced'),
        ('done', 'Done'),
        ('cancel', 'Cancelled'),
    ], string="Status")

    # ==== Report Output ====
    html_table = fields.Html("Report", readonly=True)

    def action_fetch_report(self):
        """Generate HTML table with customer sales data"""
        domain = [
            ('date_order', '>=', self.start_date),
            ('date_order', '<=', self.end_date),
        ]
        if self.branch_ids:
            domain.append(('config_id', 'in', self.branch_ids.ids))
        if self.session_ids:
            domain.append(('session_id', 'in', self.session_ids.ids))
        if self.user_ids:
            domain.append(('user_id', 'in', self.user_ids.ids))
        if self.state:
            domain.append(('state', '=', self.state))

        orders = self.env['pos.order'].search(domain)

        # ==== Totals ====
        total_discount = 0.0
        total_value = 0.0

        for order in orders:
            for line in order.lines:
                total_discount += (line.product_id.list_price - line.price_unit) * line.qty
                total_value += line.price_subtotal_incl

        # ==== HTML ====
        table_html = f"""
            <div style="margin-bottom:10px;">
                <strong>Total Discount:</strong> {total_discount:.2f} |
                <strong>Total Order Value:</strong> {total_value:.2f}
            </div>
            <table class="table table-sm table-hover table-bordered">
                <thead>
                    <tr style="background:#f0f0f0;">
                        <th>Order Date</th>
                        <th>Customer</th>
                        <th>Contact</th>
                        <th>Branch</th>
                        <th>Employee</th>
                        <th>Category</th>
                        <th>Product</th>
                        <th>Pricelist</th>
                        <th style="text-align:right;">Discount</th>
                        <th style="text-align:right;">Order Value</th>
                    </tr>
                </thead>
                <tbody>
        """
        for order in orders:
            for line in order.lines:
                table_html += f"""
                    <tr>
                        <td>{order.date_order.strftime('%Y-%m-%d')}</td>
                        <td>{order.partner_id.name or ''}</td>
                        <td>{order.partner_id.mobile or order.partner_id.phone or ''}</td>
                        <td>{order.config_id.name or ''}</td>
                        <td>{getattr(order.employee_id, 'name', '')}</td>
                        <td>{line.product_id.categ_id.name or ''}</td>
                        <td>{line.product_id.display_name or ''}</td>
                        <td>{order.pricelist_id.name or ''}</td>
                        <td style="text-align:right;">{(line.product_id.list_price - line.price_unit) * line.qty:.2f}</td>
                        <td style="text-align:right;">{line.price_subtotal_incl:.2f}</td>
                    </tr>
                """
        table_html += "</tbody></table>"

        self.html_table = table_html

    def action_generate_csv(self):
        """Generate CSV export"""
        domain = [
            ('date_order', '>=', self.start_date),
            ('date_order', '<=', self.end_date),
        ]
        orders = self.env['pos.order'].search(domain)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "Order Date", "Customer", "Contact", "Branch", "Employee",
            "Category", "Product", "Pricelist", "Discount", "Order Value"
        ])

        total_discount = 0.0
        total_value = 0.0

        for order in orders:
            for line in order.lines:
                discount = (line.product_id.list_price - line.price_unit) * line.qty
                value = line.price_subtotal_incl
                total_discount += discount
                total_value += value
                writer.writerow([
                    order.date_order.strftime('%Y-%m-%d'),
                    order.partner_id.name or '',
                    order.partner_id.mobile or order.partner_id.phone or '',
                    order.config_id.name or '',
                    getattr(order.employee_id, 'name', ''),
                    line.product_id.categ_id.name or '',
                    line.product_id.display_name or '',
                    order.pricelist_id.name or '',
                    discount,
                    value,
                ])

        # Add totals row
        writer.writerow([])
        writer.writerow(["", "", "", "", "", "", "", "TOTAL", total_discount, total_value])

        output = base64.b64encode(buffer.getvalue().encode())
        buffer.close()

        attachment = self.env['ir.attachment'].create({
            'name': f"Customer_Report_{self.start_date}_{self.end_date}.csv",
            'type': 'binary',
            'datas': output,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'text/csv'
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f"/web/content/{attachment.id}?download=true",
            'target': 'self',
        }
