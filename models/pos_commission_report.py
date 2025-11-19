import io
import csv
import base64
from datetime import date, datetime
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PosCommissionReport(models.Model):
    _name = 'pos.commission.report'
    _description = 'POS Commission Report'
    _order = 'create_date desc'
    _rec_name = 'display_name'

    # Filter fields
    start_date = fields.Date(required=True, default=fields.Date.context_today)
    end_date = fields.Date(required=True, default=fields.Date.context_today)
    category_ids = fields.Many2many('product.category', string='Product Categories')

    # Display name
    display_name = fields.Char(string='Report Name', compute='_compute_display_name', store=True)

    # Status
    report_generated = fields.Datetime(string='Generated On', readonly=True)

    # Summary fields
    total_sales = fields.Float(readonly=True)
    total_commission = fields.Float(readonly=True)
    employee_count = fields.Integer(readonly=True)

    # Employee lines
    employee_line_ids = fields.One2many('pos.commission.report.line', 'report_id', string='Employee Lines',
                                        readonly=True)

    # Export fields
    export_file = fields.Binary(readonly=True)
    export_filename = fields.Char()

    @api.depends('start_date', 'end_date')
    def _compute_display_name(self):
        for record in self:
            if record.start_date and record.end_date:
                record.display_name = f"Commission Report {record.start_date} to {record.end_date}"
            else:
                record.display_name = "New Commission Report"

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for record in self:
            if record.start_date > record.end_date:
                raise UserError(_("Start date cannot be after end date."))

    def _get_commission_data(self):
        """Get commission data for ALL employees"""
        self.ensure_one()

        # Get ALL employees first (respecting multi-company rules)
        all_employees_query = """
            SELECT
                he.id as employee_id,
                he.name as employee_name,
                COALESCE(he.individual_sale_target, 0) as target_amount,
                COALESCE(he.individual_commission_rate, 0) as commission_rate
            FROM hr_employee he
            WHERE he.active = True
            ORDER BY he.name
        """
        self.env.cr.execute(all_employees_query)
        all_employees = self.env.cr.dictfetchall()

        # Filter employees by company access
        accessible_employees = []
        for emp in all_employees:
            employee = self.env['hr.employee'].browse(emp['employee_id'])
            if employee and employee.check_access_rights('read', raise_exception=False):
                accessible_employees.append(emp)

        # Get sales data for the period
        sales_query = """
            SELECT
                po.employee_id,
                SUM(pol.price_subtotal_incl) as total_sales
            FROM pos_order_line pol
            JOIN pos_order po ON pol.order_id = po.id
            JOIN product_product pp ON pol.product_id = pp.id
            JOIN product_template pt ON pp.product_tmpl_id = pt.id
            WHERE po.date_order::date BETWEEN %s AND %s
                AND po.state IN ('paid', 'done', 'invoiced')
                AND po.employee_id IS NOT NULL
        """

        params = [self.start_date, self.end_date]

        if self.category_ids:
            sales_query += " AND pt.categ_id IN %s"
            params.append(tuple(self.category_ids.ids))

        sales_query += " GROUP BY po.employee_id"

        self.env.cr.execute(sales_query, tuple(params))
        sales_data = self.env.cr.dictfetchall()

        # Create a dictionary for quick sales lookup
        sales_dict = {sale['employee_id']: sale['total_sales'] for sale in sales_data}

        # Combine employee data with sales data
        result = []
        for emp in accessible_employees:
            employee_id = emp['employee_id']
            total_sales = sales_dict.get(employee_id, 0.0)

            result.append({
                'employee_id': employee_id,
                'employee_name': emp['employee_name'],
                'target_amount': emp['target_amount'],
                'commission_rate': emp['commission_rate'],
                'total_sales': total_sales
            })

        return result

    def action_generate_report(self):
        """Generate the complete report"""
        self.ensure_one()

        # Clear previous lines
        self.employee_line_ids.unlink()

        # Get commission data for ALL employees
        commission_data = self._get_commission_data()

        if not commission_data:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Employees Found'),
                    'message': _(
                        'No accessible employees found in the system. You may not have access to employees in other companies.'),
                    'type': 'warning',
                    'sticky': True,
                }
            }

        # Calculate totals and create lines
        total_sales = 0
        total_commission = 0
        employee_lines = []

        for emp in commission_data:
            total_sales_emp = emp['total_sales'] or 0
            target = emp['target_amount'] or 0
            rate = emp['commission_rate'] or 0

            # Calculate earned commission (only if target is met)
            earned_commission = total_sales_emp * (rate / 100) if total_sales_emp >= target else 0
            achievement_rate = (total_sales_emp / target * 100) if target > 0 else 0

            # Create employee line
            line_vals = {
                'report_id': self.id,
                'employee_id': emp['employee_id'],
                'target_amount': target,
                'commission_rate': rate,
                'total_sales': total_sales_emp,
                'earned_commission': earned_commission,
                'achievement_rate': achievement_rate,
            }
            employee_lines.append((0, 0, line_vals))

            total_sales += total_sales_emp
            total_commission += earned_commission

        # Update report fields
        self.write({
            'employee_line_ids': employee_lines,
            'total_sales': total_sales,
            'total_commission': total_commission,
            'employee_count': len(commission_data),
            'report_generated': fields.Datetime.now()
        })

        # Return action to reload the current view
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
            'views': [(False, 'form')],
        }

    def action_export_csv(self):
        """Export summary to CSV"""
        self.ensure_one()

        if not self.employee_line_ids:
            raise UserError(_("No data to export. Please generate the report first."))

        output = io.StringIO()
        writer = csv.writer(output)

        # Write headers
        headers = ['Employee', 'Target Amount', 'Commission Rate %', 'Total Sales', 'Earned Commission',
                   'Achievement Rate %']
        writer.writerow(headers)

        # Write data
        for line in self.employee_line_ids:
            writer.writerow([
                line.employee_id.sudo().name or 'Unknown',
                f"{line.target_amount:,.2f}",
                f"{line.commission_rate}%",
                f"{line.total_sales:,.2f}",
                f"{line.earned_commission:,.2f}",
                f"{line.achievement_rate:.1f}%"
            ])

        # Write totals
        writer.writerow([])
        writer.writerow(['TOTAL', '', '',
                         f"{self.total_sales:,.2f}",
                         f"{self.total_commission:,.2f}", ''])

        # Prepare file for download
        csv_data = output.getvalue().encode('utf-8')
        b64_data = base64.b64encode(csv_data)

        filename = f"commission_report_{self.start_date}_to_{self.end_date}.csv"

        self.write({
            'export_file': b64_data,
            'export_filename': filename
        })

        # Return download URL
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content?model={self._name}&id={self.id}&field=export_file&filename={filename}&download=true',
            'target': 'self',
        }

    def action_clear_filters(self):
        """Clear all filters and reset the form"""
        self.ensure_one()
        # Clear lines
        self.employee_line_ids.unlink()

        self.write({
            'start_date': fields.Date.context_today(self),
            'end_date': fields.Date.context_today(self),
            'category_ids': [(5, 0, 0)],
            'total_sales': 0,
            'total_commission': 0,
            'employee_count': 0,
            'report_generated': False,
        })

        # Return action to reload the current view
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
            'views': [(False, 'form')],
        }

    def action_view_employee_details(self):
        """View detailed sales for specific employee"""
        self.ensure_one()
        employee_id = self.env.context.get('employee_id')

        if not employee_id:
            return {'type': 'ir.actions.act_window_close'}

        # Check if user has access to this employee
        employee = self.env['hr.employee'].browse(employee_id)
        if not employee.check_access_rights('read', raise_exception=False):
            raise UserError(_("You don't have access to view details for this employee."))

        # Get detailed data for the employee
        query = """
            SELECT
                po.name as order_ref,
                po.date_order,
                pt.name as product_name,
                pc.name as category_name,
                pol.qty as quantity,
                pol.price_subtotal as subtotal_excl_tax,
                pol.price_subtotal_incl as subtotal_incl_tax,
                (pol.price_subtotal_incl - pol.price_subtotal) as tax_amount,
                pol.price_subtotal_incl as order_total
            FROM pos_order_line pol
            JOIN pos_order po ON pol.order_id = po.id
            JOIN product_product pp ON pol.product_id = pp.id
            JOIN product_template pt ON pp.product_tmpl_id = pt.id
            LEFT JOIN product_category pc ON pt.categ_id = pc.id
            WHERE po.date_order::date BETWEEN %s AND %s
                AND po.state IN ('paid', 'done', 'invoiced')
                AND po.employee_id = %s
        """

        params = [self.start_date, self.end_date, employee_id]

        if self.category_ids:
            query += " AND pt.categ_id IN %s"
            params.append(tuple(self.category_ids.ids))

        self.env.cr.execute(query, tuple(params))
        details = self.env.cr.dictfetchall()

        # Create detailed view
        return {
            'type': 'ir.actions.act_window',
            'name': _('Employee Sales Details'),
            'res_model': 'pos.employee.detail.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_employee_id': employee_id,
                'default_start_date': self.start_date,
                'default_end_date': self.end_date,
                'default_detail_lines': [(0, 0, line) for line in details]
            }
        }


class PosCommissionReportLine(models.Model):
    _name = 'pos.commission.report.line'
    _description = 'POS Commission Report Line'
    _order = 'total_sales desc'

    report_id = fields.Many2one('pos.commission.report', string='Report', required=True, ondelete='cascade')
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True)
    target_amount = fields.Float(string='Target Amount')
    commission_rate = fields.Float(string='Commission Rate %')
    total_sales = fields.Float(string='Total Sales')
    earned_commission = fields.Float(string='Earned Commission')
    achievement_rate = fields.Float(string='Achievement Rate %')

    def action_view_details(self):
        """Open employee details wizard"""
        self.ensure_one()
        return self.report_id.with_context(employee_id=self.employee_id.id).action_view_employee_details()


class PosEmployeeDetailWizard(models.TransientModel):
    _name = 'pos.employee.detail.wizard'
    _description = 'POS Employee Detail Wizard'

    employee_id = fields.Many2one('hr.employee', string='Employee', readonly=True)
    start_date = fields.Date(readonly=True)
    end_date = fields.Date(readonly=True)

    # Summary fields
    total_orders = fields.Integer(string='Total Orders', compute='_compute_summary')
    total_quantity = fields.Float(string='Total Quantity', compute='_compute_summary')
    total_subtotal = fields.Float(string='Total Subtotal', compute='_compute_summary')
    total_tax = fields.Float(string='Total Tax', compute='_compute_summary')
    total_amount = fields.Float(string='Total Amount', compute='_compute_summary')

    detail_lines = fields.One2many('pos.employee.detail.line', 'wizard_id', string='Detail Lines')

    # Export fields
    export_file = fields.Binary(readonly=True)
    export_filename = fields.Char()

    @api.depends('detail_lines')
    def _compute_summary(self):
        for wizard in self:
            lines = wizard.detail_lines
            wizard.total_orders = len(set(line.order_ref for line in lines))
            wizard.total_quantity = sum(line.quantity for line in lines)
            wizard.total_subtotal = sum(line.subtotal_excl_tax for line in lines)
            wizard.total_tax = sum(line.tax_amount for line in lines)
            wizard.total_amount = sum(line.order_total for line in lines)

    def action_export_details(self):
        """Export detailed data to CSV"""
        self.ensure_one()

        output = io.StringIO()
        writer = csv.writer(output)

        headers = ['Order Reference', 'Date', 'Product', 'Category', 'Quantity',
                   'Subtotal (Excl Tax)', 'Subtotal (Incl Tax)', 'Tax Amount', 'Total']
        writer.writerow(headers)

        for line in self.detail_lines:
            writer.writerow([
                line.order_ref or '',
                line.date_order.strftime('%Y-%m-%d %H:%M:%S') if line.date_order else '',
                line.product_name or '',
                line.category_name or '',
                line.quantity,
                f"{line.subtotal_excl_tax:,.2f}",
                f"{line.subtotal_incl_tax:,.2f}",
                f"{line.tax_amount:,.2f}",
                f"{line.order_total:,.2f}"
            ])

        # Write summary
        writer.writerow([])
        writer.writerow(['SUMMARY', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Orders', self.total_orders, '', '', '', '', '', '', ''])
        writer.writerow(['Total Quantity', self.total_quantity, '', '', '', '', '', '', ''])
        writer.writerow(['Total Subtotal', f"{self.total_subtotal:,.2f}", '', '', '', '', '', '', ''])
        writer.writerow(['Total Tax', f"{self.total_tax:,.2f}", '', '', '', '', '', '', ''])
        writer.writerow(['Total Amount', f"{self.total_amount:,.2f}", '', '', '', '', '', '', ''])

        csv_data = output.getvalue().encode('utf-8')
        b64_data = base64.b64encode(csv_data)

        filename = f"employee_details_{self.employee_id.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        self.write({
            'export_file': b64_data,
            'export_filename': filename
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content?model={self._name}&id={self.id}&field=export_file&filename={filename}&download=true',
            'target': 'self',
        }


class PosEmployeeDetailLine(models.TransientModel):
    _name = 'pos.employee.detail.line'
    _description = 'POS Employee Detail Line'

    wizard_id = fields.Many2one('pos.employee.detail.wizard', string='Wizard', required=True, ondelete='cascade')
    order_ref = fields.Char(string='Order Reference')
    date_order = fields.Datetime(string='Order Date')
    product_name = fields.Char(string='Product')
    category_name = fields.Char(string='Category')
    quantity = fields.Float(string='Quantity')
    subtotal_excl_tax = fields.Float(string='Subtotal (Excl Tax)')
    subtotal_incl_tax = fields.Float(string='Subtotal (Incl Tax)')
    tax_amount = fields.Float(string='Tax Amount')
    order_total = fields.Float(string='Total')