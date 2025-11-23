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
    start_date = fields.Datetime(string="Start Date", required=True, default=fields.Datetime.now)
    end_date = fields.Datetime(string="End Date", required=True, default=fields.Datetime.now)
    branch_ids = fields.Many2many("pos.config", string="POS Branches")
    employee_ids = fields.Many2many("hr.employee", string="Employees")

    # ==== Summary Fields ====
    total_employees = fields.Integer(string="Total Employees", readonly=True)
    total_orders = fields.Integer(string="Total Orders", readonly=True)
    total_quantity = fields.Float(string="Total Quantity", readonly=True)
    total_sales = fields.Float(string="Total Sales", readonly=True)
    total_commission = fields.Float(string="Total Commission", readonly=True)

    # ==== Report Lines ====
    report_line_ids = fields.One2many("pos.staff.performance.report.line", "report_id", string="Report Lines",
                                      readonly=True)

    # ==== Export Fields ====
    export_file = fields.Binary(readonly=True)
    export_filename = fields.Char()

    @api.depends('start_date', 'end_date')
    def _compute_name(self):
        for record in self:
            if record.start_date and record.end_date:
                record.name = f"Staff Performance {record.start_date.date()} to {record.end_date.date()}"
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
            "po.date_order >= %s",
            "po.date_order <= %s",
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
        """Generate the complete report with detailed transaction data"""
        self.ensure_one()

        # Clear previous lines
        self.report_line_ids.unlink()

        where_clause, params = self._build_where_clause()

        # Detailed query to get transaction-level data matching your original fields
        query = f"""
            SELECT 
                -- Employee Details
                he.id as employee_id,
                he.name as employee_name,
                he.barcode as employee_batch_no,
                dj.name->>'en_US' as job_position,
                dep.name->>'en_US' as department_name,
                he.work_email,
                he.work_phone,
                he.identification_id as employee_national_id,

                -- Order Details
                po.id as order_id,
                po.name as order_name,
                po.pos_reference,
                po.amount_total as order_total,
                po.date_order as order_date,

                -- Session and Branch Details
                ps.name as session_name,
                pc.name as pos_branch,

                -- Payment Details
                ppm.name->>'en_US' as payment_method,
                ppay.amount as payment_amount,

                -- Customer Details
                po.partner_id,
                rp.name as customer_name,
                rp.phone as customer_phone,
                rp.mobile as customer_mobile,
                rp.email as customer_email,

                -- Product Line Details
                pol.id as line_id,
                pt.name->>'en_US' as product_name,
                pcateg.name as product_category,
                pt.type as product_type,
                pt.default_code as product_internal_code,
                pol.qty as quantity,
                pol.price_unit as unit_price,
                (pol.qty * pol.price_unit) as line_total,
                
                -- Performance Metrics
                SUM(pol.qty * pol.price_unit) OVER (PARTITION BY he.id) as employee_total_sale,

                -- Additional fields for your XML view
                he.individual_commission_rate as commission_rate,
                he.individual_sale_target as sales_target,
                (pol.qty * pol.price_unit * he.individual_commission_rate / 100) as earned_commission

            FROM pos_order po
            LEFT JOIN pos_order_line pol ON po.id = pol.order_id
            LEFT JOIN hr_employee he ON po.employee_id = he.id
            LEFT JOIN hr_job dj ON he.job_id = dj.id
            LEFT JOIN hr_department dep ON he.department_id = dep.id
            LEFT JOIN pos_config pc ON po.config_id = pc.id
            LEFT JOIN pos_session ps ON po.session_id = ps.id
            LEFT JOIN pos_payment ppay ON ppay.pos_order_id = po.id
            LEFT JOIN pos_payment_method ppm ON ppay.payment_method_id = ppm.id
            LEFT JOIN res_partner rp ON po.partner_id = rp.id
            LEFT JOIN product_product pp ON pol.product_id = pp.id
            LEFT JOIN product_template pt ON pp.product_tmpl_id = pt.id
            LEFT JOIN product_category pcateg ON pt.categ_id = pcateg.id
            WHERE {where_clause}
            ORDER BY pc.name, he.name, po.date_order, pol.id
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

        # Calculate totals
        unique_employees = set(row['employee_id'] for row in results if row['employee_id'])
        unique_orders = set(row['order_id'] for row in results if row['order_id'])

        total_employees = len(unique_employees)
        total_orders = len(unique_orders)
        total_quantity = sum(row['quantity'] or 0 for row in results)
        total_sales = sum(row['line_total'] or 0 for row in results)
        total_commission = sum(row['earned_commission'] or 0 for row in results)

        # Create detailed report lines
        report_lines = []
        for row in results:
            line_vals = {
                'report_id': self.id,
                # Employee Details
                'employee_name': row['employee_name'],
                'employee_batch_no': row['employee_batch_no'],
                'job_position': row['job_position'],
                'department_name': row['department_name'],
                'work_email': row['work_email'],
                'work_phone': row['work_phone'],
                'employee_national_id': row['employee_national_id'],

                # Order Details
                'order_name': row['order_name'],
                'pos_reference': row['pos_reference'],
                'order_total': row['order_total'] or 0,
                'order_date': row['order_date'],

                # Session and Branch Details
                'session_name': row['session_name'],
                'branch_name': row['pos_branch'],

                # Payment Details
                'payment_method': row['payment_method'],
                'payment_amount': row['payment_amount'] or 0,

                # Customer Details
                'customer_name': row['customer_name'] or 'Walk-in Customer',
                'customer_phone': row['customer_phone'],
                'customer_mobile': row['customer_mobile'],
                'customer_email': row['customer_email'],

                # Product Details
                'product_name': row['product_name'],
                'product_category': row['product_category'],
                'product_type': row['product_type'],
                'product_internal_code': row['product_internal_code'],
                'quantity': row['quantity'] or 0,
                'unit_price': row['unit_price'] or 0,
                'line_total': row['line_total'] or 0,

                # Performance Metrics
                'employee_total_sale': row['employee_total_sale'] or 0,
                'commission_rate': row['commission_rate'] or 0,
                'earned_commission': row['earned_commission'] or 0,
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
        """Export detailed report to CSV"""
        self.ensure_one()

        if not self.report_line_ids:
            raise UserError(_("No data to export. Please generate the report first."))

        output = io.StringIO()
        writer = csv.writer(output)

        # Write headers for detailed report with all fields
        headers = [
            # Employee Details
            'Employee Name', 'Employee Batch No', 'Job Position', 'Department',
            'Work Email', 'Work Phone', 'National ID',

            # Order Details
            'Order Name', 'POS Reference', 'Order Total', 'Order Date',

            # Session and Branch
            'Session Name', 'Branch Name',

            # Payment Details
            'Payment Method', 'Payment Amount',

            # Customer Details
            'Customer Name', 'Customer Phone', 'Customer Mobile', 'Customer Email',

            # Product Details
            'Product Name', 'Product Category', 'Product Type', 'Product Internal Code',
            'Quantity', 'Unit Price', 'Line Total', 'UOM Name',

            # Performance Metrics
            'Employee Total Sale', 'Commission Rate %', 'Earned Commission'
        ]
        writer.writerow(headers)

        # Write data rows
        for line in self.report_line_ids:
            writer.writerow([
                # Employee Details
                line.employee_name or '',
                line.employee_batch_no or '',
                line.job_position or '',
                line.department_name or '',
                line.work_email or '',
                line.work_phone or '',
                line.employee_national_id or '',

                # Order Details
                line.order_name or '',
                line.pos_reference or '',
                f"{line.order_total:,.2f}",
                line.order_date or '',

                # Session and Branch
                line.session_name or '',
                line.branch_name or '',

                # Payment Details
                line.payment_method or '',
                f"{line.payment_amount:,.2f}",

                # Customer Details
                line.customer_name or '',
                line.customer_phone or '',
                line.customer_mobile or '',
                line.customer_email or '',

                # Product Details
                line.product_name or '',
                line.product_category or '',
                line.product_type or '',
                line.product_internal_code or '',
                line.quantity,
                f"{line.unit_price:,.2f}",
                f"{line.line_total:,.2f}",

                # Performance Metrics
                f"{line.employee_total_sale:,.2f}",
                f"{line.commission_rate:,.2f}",
                f"{line.earned_commission:,.2f}",
            ])

        # Write summary
        writer.writerow([])
        writer.writerow(['REPORT SUMMARY'])
        writer.writerow(['Total Employees', self.total_employees])
        writer.writerow(['Total Orders', self.total_orders])
        writer.writerow(['Total Quantity', self.total_quantity])
        writer.writerow(['Total Sales', f"{self.total_sales:,.2f}"])
        writer.writerow(['Total Commission', f"{self.total_commission:,.2f}"])

        # Prepare file for download
        csv_data = output.getvalue().encode('utf-8')
        b64_data = base64.b64encode(csv_data)

        filename = f"detailed_staff_performance_{self.start_date.date()}_to_{self.end_date.date()}.csv"

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
            'start_date': fields.Datetime.now(),
            'end_date': fields.Datetime.now(),
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
            'start_date': fields.Datetime.now(),
            'end_date': fields.Datetime.now(),
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
    _order = "branch_name, employee_name, order_date"

    report_id = fields.Many2one("pos.staff.performance.report", string="Report", required=True, ondelete="cascade")

    # Employee Details
    employee_name = fields.Char(string="Employee Name")
    employee_batch_no = fields.Char(string="Employee Batch No")
    work_email = fields.Char(string="Work Email")
    work_phone = fields.Char(string="Work Phone")
    employee_national_id = fields.Char(string="National ID")
    job_position = fields.Char(string="Job Position")
    department_name = fields.Char(string="Department")

    # Order Details
    order_name = fields.Char(string="Order Name")
    pos_reference = fields.Char(string="POS Reference")
    order_total = fields.Float(string="Order Total")
    order_date = fields.Datetime(string="Order Date")

    # Session and Branch Details
    session_name = fields.Char(string="Session")
    branch_name = fields.Char(string="Branch")

    # Payment Details
    payment_method = fields.Char(string="Payment Method")
    payment_amount = fields.Float(string="Payment Amount")

    # Customer Details
    customer_name = fields.Char(string="Customer Name")
    customer_phone = fields.Char(string="Customer Phone")
    customer_mobile = fields.Char(string="Customer Mobile")
    customer_email = fields.Char(string="Customer Email")

    # Product Details
    product_name = fields.Char(string="Product Name")
    product_category = fields.Char(string="Product Category")
    product_type = fields.Char(string="Product Type")
    product_internal_code = fields.Char(string="Product Internal Code")
    quantity = fields.Float(string="Quantity")
    unit_price = fields.Float(string="Unit Price")
    line_total = fields.Float(string="Line Total")

    # Performance Metrics
    employee_total_sale = fields.Float(string="Employee Total Sale")
    commission_rate = fields.Float(string="Commission Rate %")
    earned_commission = fields.Float(string="Earned Commission")