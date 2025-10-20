# -*- coding: utf-8 -*-
from odoo import models, fields, api
import base64
import csv
import io

class PosSalesReportLine(models.TransientModel):
    _name = 'pos.sales.report.line'
    _description = 'POS Sales Report Line'

    wizard_id = fields.Many2one('pos.sales.report.wizard', ondelete='cascade')

    employee_name = fields.Char("Employee")
    employee_barcode = fields.Char("Barcode")
    order_id = fields.Integer("Order ID")
    order_reference = fields.Char("Order Reference")
    order_date = fields.Date("Order Date")
    customer_name = fields.Char("Customer")
    pos_config_name = fields.Char("POS Config")
    session_name = fields.Char("Session")
    cashier_login = fields.Char("Cashier")
    pricelist_name = fields.Char("Pricelist")
    product_name = fields.Char("Product")
    category_name = fields.Char("Category")
    quantity_sold = fields.Float("Quantity Sold")
    original_price_unit = fields.Float("Original Price Unit")
    actual_price_unit = fields.Float("Actual Price Unit")
    unit_discount = fields.Float("Unit Discount")
    total_before_discount = fields.Float("Total Before Discount")
    subtotal_excl_tax = fields.Float("Subtotal Excl. Tax")
    subtotal_incl_tax = fields.Float("Subtotal Incl. Tax")
    tax_value = fields.Float("Tax Value")
    order_total = fields.Float("Order Total")
    order_state = fields.Char("Order State")


class PosSalesReportWizard(models.TransientModel):
    _name = 'pos.sales.report.wizard'
    _description = 'POS Sales Report Wizard'

    start_date = fields.Date("Start Date", required=True)
    end_date = fields.Date("End Date", required=True)
    category_ids = fields.Many2many('product.category', string="Categories")
    employee_ids = fields.Many2many('hr.employee', string="Employees")

    line_ids = fields.One2many('pos.sales.report.line', 'wizard_id', string="Report Lines")

    file_data = fields.Binary("CSV File")
    file_name = fields.Char("File Name")

    # --- Fetch Details ---
    def action_fetch_details(self):
        self.ensure_one()
        query = """
            SELECT
                he.name AS employee_name,
                he.barcode AS employee_barcode,
                po.id AS order_id,
                po.name AS order_reference,
                po.date_order::date AS order_date,
                rp.name AS customer_name,
                pc.name AS pos_config_name,
                ps.name AS session_name,
                ru.login AS cashier_login,
                pl.name AS pricelist_name,
                pt.name AS product_name,
                pcateg.name AS category_name,
                pol.qty AS quantity_sold,
                pt.list_price AS original_price_unit,
                pol.price_unit AS actual_price_unit,
                (pt.list_price - pol.price_unit) AS unit_discount,
                (pt.list_price * pol.qty) AS total_before_discount,
                pol.price_subtotal AS subtotal_excl_tax,
                pol.price_subtotal_incl AS subtotal_incl_tax,
                (pol.price_subtotal_incl - pol.price_subtotal) AS tax_value,
                po.amount_total AS order_total,
                po.state AS order_state
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
            LEFT JOIN product_category pcateg ON pt.categ_id = pcateg.id
            LEFT JOIN hr_employee he ON pol.note = he.barcode
            WHERE po.date_order::date BETWEEN %s AND %s
              AND po.state NOT IN ('cancel')
        """

        params = [self.start_date, self.end_date]

        if self.category_ids:
            query += " AND pt.categ_id IN %s"
            params.append(tuple(self.category_ids.ids))

        if self.employee_ids:
            query += " AND he.id IN %s"
            params.append(tuple(self.employee_ids.ids))

        query += " ORDER BY he.name, po.date_order, po.name, pt.name"

        self.env.cr.execute(query, tuple(params))
        rows = self.env.cr.dictfetchall()

        # Remove old lines
        self.line_ids.unlink()

        for r in rows:
            self.env['pos.sales.report.line'].create({
                'wizard_id': self.id,
                **r,
            })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pos.sales.report.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
        }

    # --- Generate CSV ---
    def action_generate_csv(self):
        """Redirect to controller for CSV download"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': f'/pos_sales_report/download_csv/{self.id}',
            'target': 'new',
        }