# -*- coding: utf-8 -*-
from odoo import models, fields, api
import base64
import csv
import io


# --------------------------------------------------------
# Employee-wise Summary Line
# --------------------------------------------------------
class PosSalesReportLine(models.TransientModel):
    _name = 'pos.sales.report.line'
    _description = 'POS Sales Report Line'

    wizard_id = fields.Many2one('pos.sales.report.wizard', ondelete='cascade')

    employee_id = fields.Many2one('hr.employee', string="Employee")
    employee_name = fields.Char("Employee")
    employee_barcode = fields.Char("Barcode")

    target_commission = fields.Float("Target Commission")
    commission_rate = fields.Float("Commission Rate (%)")
    total_sales = fields.Float("Total Sales")
    earned_commission = fields.Float("Earned Commission")

    def action_view_employee_details(self):
        """Open a detailed sales report for this employee"""
        self.ensure_one()
        wizard = self.wizard_id

        # Remove old details for this wizard
        self.env['pos.sales.report.detail'].search([('wizard_id', '=', wizard.id)]).unlink()

        # Build category filter
        category_filter = ""
        params = [wizard.start_date, wizard.end_date, self.employee_id.id]
        if wizard.category_ids:
            category_filter = "AND pt.categ_id IN %s"
            params.append(tuple(wizard.category_ids.ids))

        query = f"""
            SELECT
                po.name AS order_reference,
                po.date_order::date AS order_date,
                pt.name AS product_name,
                pcateg.name AS category_name,
                pol.qty AS quantity_sold,
                pol.price_subtotal AS subtotal_excl_tax,
                pol.price_subtotal_incl AS subtotal_incl_tax,
                (pol.price_subtotal_incl - pol.price_subtotal) AS tax_value,
                po.amount_total AS order_total
            FROM
                pos_order_line pol
            JOIN pos_order po ON pol.order_id = po.id
            JOIN product_product pp ON pol.product_id = pp.id
            JOIN product_template pt ON pp.product_tmpl_id = pt.id
            LEFT JOIN product_category pcateg ON pt.categ_id = pcateg.id
            LEFT JOIN hr_employee he ON pol.note = he.barcode
            WHERE po.date_order::date BETWEEN %s AND %s
              AND he.id = %s
              AND po.state NOT IN ('cancel')
              {category_filter}
            ORDER BY po.date_order, po.name
        """

        self.env.cr.execute(query, tuple(params))
        rows = self.env.cr.dictfetchall()

        for r in rows:
            self.env['pos.sales.report.detail'].create({
                'wizard_id': wizard.id,
                'employee_name': self.employee_name,
                **r
            })

        wizard._compute_totals()

        return {
            'type': 'ir.actions.act_window',
            'name': f"{self.employee_name} - POS Sales Details",
            'res_model': 'pos.sales.report.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'current',
            'views': [(self.env.ref('myreports.view_pos_sales_report_wizard_form_with_details').id, 'form')],
        }


# --------------------------------------------------------
# Detailed Employee Sales Lines
# --------------------------------------------------------
class PosSalesReportDetail(models.TransientModel):
    _name = 'pos.sales.report.detail'
    _description = 'POS Sales Report Detailed Lines'

    wizard_id = fields.Many2one('pos.sales.report.wizard', ondelete='cascade')
    employee_name = fields.Char("Employee")

    order_reference = fields.Char("Order Reference")
    order_date = fields.Date("Order Date")
    product_name = fields.Char("Product")
    category_name = fields.Char("Category")
    quantity_sold = fields.Float("Quantity Sold")
    subtotal_excl_tax = fields.Float("Subtotal Excl. Tax")
    subtotal_incl_tax = fields.Float("Subtotal Incl. Tax")
    tax_value = fields.Float("Tax Value")
    order_total = fields.Float("Order Total")


# --------------------------------------------------------
# Wizard Main
# --------------------------------------------------------
class PosSalesReportWizard(models.TransientModel):
    _name = 'pos.sales.report.wizard'
    _description = 'POS Sales Report Wizard'

    start_date = fields.Date("Start Date", required=True)
    end_date = fields.Date("End Date", required=True)
    category_ids = fields.Many2many('product.category', string="Categories")

    line_ids = fields.One2many('pos.sales.report.line', 'wizard_id', string="Report Lines")
    detail_line_ids = fields.One2many('pos.sales.report.detail', 'wizard_id', string="Detail Lines")

    file_data = fields.Binary("CSV File")
    file_name = fields.Char("File Name")

    total_subtotal_excl_tax = fields.Float("Total Subtotal (Excl. Tax)", compute="_compute_totals", store=True)
    total_tax_value = fields.Float("Total Tax", compute="_compute_totals", store=True)
    total_order_total = fields.Float("Total Order Amount", compute="_compute_totals", store=True)
    total_sales_all = fields.Float("Total Sales (All Employees)", compute="_compute_total_sales_all", store=True)

    @api.depends('detail_line_ids')
    def _compute_totals(self):
        for wizard in self:
            details = wizard.detail_line_ids
            wizard.total_subtotal_excl_tax = sum(d.subtotal_excl_tax or 0.0 for d in details)
            wizard.total_tax_value = sum(d.tax_value or 0.0 for d in details)
            wizard.total_order_total = sum(d.order_total or 0.0 for d in details)

    @api.depends('line_ids.total_sales')
    def _compute_total_sales_all(self):
        for wizard in self:
            wizard.total_sales_all = sum(line.total_sales for line in wizard.line_ids)

    # --- Fetch Summary ---
    def action_fetch_details(self):
        self.ensure_one()
        category_filter = ""
        params = [self.start_date, self.end_date]

        if self.category_ids:
            category_filter = "AND pt.categ_id IN %s"
            params.append(tuple(self.category_ids.ids))

        query = f"""
            SELECT
                he.id AS employee_id,
                he.name AS employee_name,
                he.barcode AS employee_barcode,
                he.individual_sale_target AS target_commission,
                he.individual_commission_rate AS commission_rate,
                SUM(pol.price_subtotal_incl) AS total_sales
            FROM
                pos_order_line pol
            JOIN pos_order po ON pol.order_id = po.id
            JOIN product_product pp ON pol.product_id = pp.id
            JOIN product_template pt ON pp.product_tmpl_id = pt.id
            LEFT JOIN hr_employee he ON pol.note = he.barcode
            WHERE po.date_order::date BETWEEN %s AND %s
              AND po.state NOT IN ('cancel')
              {category_filter}
            GROUP BY he.id, he.name, he.barcode, he.individual_sale_target, he.individual_commission_rate
            ORDER BY he.name
        """

        self.env.cr.execute(query, tuple(params))
        employee_rows = self.env.cr.dictfetchall()
        self.line_ids.unlink()
        self.detail_line_ids.unlink()

        for emp in employee_rows:
            total_sales = emp.get('total_sales', 0) or 0
            target = emp.get('target_commission', 0) or 0
            rate = emp.get('commission_rate', 0) or 0
            earned = total_sales * rate / 100 if total_sales >= target else 0

            self.env['pos.sales.report.line'].create({
                'wizard_id': self.id,
                'employee_id': emp['employee_id'],
                'employee_name': emp['employee_name'],
                'employee_barcode': emp['employee_barcode'],
                'target_commission': target,
                'commission_rate': rate,
                'total_sales': total_sales,
                'earned_commission': earned,
            })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pos.sales.report.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
        }

    # --- Summary CSV ---
    def action_generate_csv(self):
        self.ensure_one()
        output = io.StringIO()
        writer = csv.writer(output)

        headers = ['Employee', 'Barcode', 'Target', 'Rate (%)', 'Total Sales', 'Earned Commission']
        writer.writerow(headers)

        for line in self.line_ids:
            writer.writerow([
                line.employee_name, line.employee_barcode,
                line.target_commission, line.commission_rate,
                line.total_sales, line.earned_commission
            ])

        data = output.getvalue().encode('utf-8')
        self.file_data = base64.b64encode(data)
        self.file_name = 'POS_Employee_Commission_Report.csv'

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/?model=pos.sales.report.wizard&id={self.id}&field=file_data&filename_field=file_name&download=true',
            'target': 'new',
        }

    # --- Detailed CSV ---
    def action_generate_detail_csv(self):
        """Generate CSV for the currently viewed employee's detail lines only"""
        self.ensure_one()
        output = io.StringIO()
        writer = csv.writer(output)

        headers = [
            'Employee Name', 'Order Reference', 'Order Date', 'Product', 'Category',
            'Quantity', 'Subtotal Excl. Tax', 'Subtotal Incl. Tax', 'Tax Value', 'Order Total'
        ]
        writer.writerow(headers)

        for line in self.detail_line_ids:
            writer.writerow([
                line.employee_name,
                line.order_reference,
                line.order_date,
                line.product_name,
                line.category_name,
                line.quantity_sold,
                line.subtotal_excl_tax,
                line.subtotal_incl_tax,
                line.tax_value,
                line.order_total
            ])

        data = output.getvalue().encode('utf-8')
        self.file_data = base64.b64encode(data)
        self.file_name = f"POS_Sales_Detail_{self.detail_line_ids[:1].employee_name or 'Employee'}.csv"

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/?model=pos.sales.report.wizard&id={self.id}&field=file_data&filename_field=file_name&download=true',
            'target': 'new',
        }
