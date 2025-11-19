import io
import csv
import base64
from datetime import date, datetime
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PosStaffPerformanceReport(models.Model):
    _name = "pos.staff.performance.report"
    _description = "POS Staff Performance Report"
    _order = "create_date desc"
    _rec_name = "name"

    # ==== Basic Fields ====
    name = fields.Char(string="Report Name", compute="_compute_name", store=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('generated', 'Generated')
    ], string="Status", default='draft')
    report_generated = fields.Datetime(string="Generated On", readonly=True)

    # ==== Filter Fields ====
    start_date = fields.Date(string="Start Date", required=True, default=fields.Date.context_today)
    end_date = fields.Date(string="End Date", required=True, default=fields.Date.context_today)
    branch_ids = fields.Many2many("pos.config", string="POS Branches")
    employee_ids = fields.Many2many("hr.employee", string="Employees")

    # ==== Summary Fields ====
    total_employees = fields.Integer(string="Total Employees", readonly=True)
    total_orders = fields.Integer(string="Total Orders", readonly=True)
    total_quantity = fields.Float(string="Total Quantity", readonly=True)
    total_sales = fields.Float(string="Total Sales", readonly=True)
    total_commission = fields.Float(string="Total Commission", readonly=True)

    # ==== Report Lines ====
    report_line_ids = fields.One2many("pos.staff.performance.report.line", "report_id", string="Report Lines", readonly=True)

    # ==== Export Fields ====
    export_file = fields.Binary(readonly=True)
    export_filename = fields.Char()

    @api.depends('start_date', 'end_date')
    def _compute_name(self):
        for record in self:
            if record.start_date and record.end_date:
                record.name = f"Staff Performance {record.start_date} to {record.end_date}"
            else:
                record.name = "New Staff Performance Report"

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for record in self:
            if record.start_date > record.end_date:
                raise UserError(_("Start date cannot be after end date."))

    def _build_where_clause(self):
        """Build WHERE clause for the SQL query"""
        where_clauses = [
            "po.date_order::date >= %s",
            "po.date_order::date <= %s",
            "po.state = 'paid'"
        ]
        params = [self.start_date, self.end_date]

        if self.branch_ids:
            where_clauses.append("po.config_id IN %s")
            params.append(tuple(self.branch_ids.ids))
        if self.employee_ids:
            where_clauses.append("po.employee_id IN %s")
            params.append(tuple(self.employee_ids.ids))

        return " AND ".join(where_clauses), params

    def action_generate_report(self):
        """Generate the complete report"""
        self.ensure_one()

        # Clear previous lines
        self.report_line_ids.unlink()

        where_clause, params = self._build_where_clause()

        # Main query to get staff performance data
        query = f"""
            SELECT 
                he.id as employee_id,
                he.name as employee_name,
                he.work_email,
                he.work_phone,
                he.identification_id as employee_national_id,
                dj.name->>'en_US' as job_position,
                dep.name->>'en_US' as department_name,
                pc.name as branch_name,
                COUNT(DISTINCT po.id) as total_orders,
                SUM(pol.qty) as total_quantity,
                SUM(pol.price_subtotal_incl) as total_sales,
                AVG(he.individual_commission_rate) as commission_rate,
                AVG(he.individual_sale_target) as sales_target,
                SUM(pol.price_subtotal_incl) * AVG(he.individual_commission_rate) / 100 as earned_commission,
                CASE 
                    WHEN AVG(he.individual_sale_target) > 0 THEN 
                        (SUM(pol.price_subtotal_incl) / AVG(he.individual_sale_target)) * 100 
                    ELSE 0 
                END as target_achievement_rate
            FROM pos_order po
            LEFT JOIN pos_order_line pol ON po.id = pol.order_id
            LEFT JOIN hr_employee he ON po.employee_id = he.id
            LEFT JOIN hr_job dj ON he.job_id = dj.id
            LEFT JOIN hr_department dep ON he.department_id = dep.id
            LEFT JOIN pos_config pc ON po.config_id = pc.id
            WHERE {where_clause}
            GROUP BY 
                he.id, he.name, he.work_email, he.work_phone, he.identification_id,
                dj.name, dep.name, pc.name
            ORDER BY total_sales DESC
        """

        self.env.cr.execute(query, tuple(params))
        results = self.env.cr.dictfetchall()

        if not results:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Data Found'),
                    'message': _('No performance data found for the selected period and filters.'),
                    'type': 'warning',
                    'sticky': True,
                }
            }

        # Calculate totals and create lines
        total_employees = len(results)
        total_orders = sum(row['total_orders'] or 0 for row in results)
        total_quantity = sum(row['total_quantity'] or 0 for row in results)
        total_sales = sum(row['total_sales'] or 0 for row in results)
        total_commission = sum(row['earned_commission'] or 0 for row in results)

        report_lines = []
        for row in results:
            line_vals = {
                'report_id': self.id,
                'employee_name': row['employee_name'],
                'work_email': row['work_email'],
                'work_phone': row['work_phone'],
                'employee_national_id': row['employee_national_id'],
                'job_position': row['job_position'],
                'department_name': row['department_name'],
                'branch_name': row['branch_name'],
                'total_orders': row['total_orders'] or 0,
                'total_quantity': row['total_quantity'] or 0,
                'total_sales': row['total_sales'] or 0,
                'commission_rate': row['commission_rate'] or 0,
                'sales_target': row['sales_target'] or 0,
                'earned_commission': row['earned_commission'] or 0,
                'target_achievement_rate': row['target_achievement_rate'] or 0,
            }
            report_lines.append((0, 0, line_vals))

        # Update report with new data
        self.write({
            'report_line_ids': report_lines,
            'total_employees': total_employees,
            'total_orders': total_orders,
            'total_quantity': total_quantity,
            'total_sales': total_sales,
            'total_commission': total_commission,
            'state': 'generated',
            'report_generated': fields.Datetime.now(),
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
        """Export report to CSV"""
        self.ensure_one()

        if not self.report_line_ids:
            raise UserError(_("No data to export. Please generate the report first."))

        output = io.StringIO()
        writer = csv.writer(output)

        # Write headers
        headers = [
            'Employee Name', 'Job Position', 'Department', 'Branch', 
            'Work Email', 'Work Phone', 'National ID',
            'Total Orders', 'Total Quantity', 'Total Sales',
            'Commission Rate %', 'Sales Target', 'Earned Commission',
            'Target Achievement Rate %'
        ]
        writer.writerow(headers)

        # Write data rows
        for line in self.report_line_ids:
            writer.writerow([
                line.employee_name,
                line.job_position or '',
                line.department_name or '',
                line.branch_name or '',
                line.work_email or '',
                line.work_phone or '',
                line.employee_national_id or '',
                line.total_orders,
                line.total_quantity,
                f"{line.total_sales:,.2f}",
                f"{line.commission_rate:,.2f}%",
                f"{line.sales_target:,.2f}",
                f"{line.earned_commission:,.2f}",
                f"{line.target_achievement_rate:,.1f}%",
            ])

        # Write summary
        writer.writerow([])
        writer.writerow(['REPORT SUMMARY', '', '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Employees', self.total_employees, '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Orders', self.total_orders, '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Quantity', self.total_quantity, '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Sales', f"{self.total_sales:,.2f}", '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Commission', f"{self.total_commission:,.2f}", '', '', '', '', '', '', '', '', '', '', '', ''])

        # Prepare file for download
        csv_data = output.getvalue().encode('utf-8')
        b64_data = base64.b64encode(csv_data)

        filename = f"staff_performance_{self.start_date}_to_{self.end_date}.csv"

        self.write({
            'export_file': b64_data,
            'export_filename': filename
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content?model={self._name}&id={self.id}&field=export_file&filename={filename}&download=true',
            'target': 'self',
        }

    def action_clear_filters(self):
        """Clear all filters and reset the form"""
        self.ensure_one()

        # Clear lines
        self.report_line_ids.unlink()

        self.write({
            'start_date': fields.Date.context_today(self),
            'end_date': fields.Date.context_today(self),
            'branch_ids': [(5, 0, 0)],
            'employee_ids': [(5, 0, 0)],
            'total_employees': 0,
            'total_orders': 0,
            'total_quantity': 0,
            'total_sales': 0,
            'total_commission': 0,
            'state': 'draft',
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

    @api.model
    def create_report_action(self):
        """Create a new report and open it in form view"""
        report = self.create({
            'start_date': fields.Date.context_today(self),
            'end_date': fields.Date.context_today(self),
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Staff Performance Report'),
            'res_model': 'pos.staff.performance.report',
            'res_id': report.id,
            'view_mode': 'form',
            'target': 'current',
        }


class PosStaffPerformanceReportLine(models.Model):
    _name = "pos.staff.performance.report.line"
    _description = "POS Staff Performance Report Line"
    _order = "total_sales desc"

    report_id = fields.Many2one("pos.staff.performance.report", string="Report", required=True, ondelete="cascade")
    employee_name = fields.Char(string="Employee Name")
    work_email = fields.Char(string="Work Email")
    work_phone = fields.Char(string="Work Phone")
    employee_national_id = fields.Char(string="National ID")
    job_position = fields.Char(string="Job Position")
    department_name = fields.Char(string="Department")
    branch_name = fields.Char(string="Branch")
    total_orders = fields.Integer(string="Total Orders")
    total_quantity = fields.Float(string="Total Quantity")
    total_sales = fields.Float(string="Total Sales")
    commission_rate = fields.Float(string="Commission Rate %")
    sales_target = fields.Float(string="Sales Target")
    earned_commission = fields.Float(string="Earned Commission")
    target_achievement_rate = fields.Float(string="Target Achievement %")