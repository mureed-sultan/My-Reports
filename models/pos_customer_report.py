import io
import csv
import base64
from datetime import date, datetime
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PosCustomerReport(models.Model):
    _name = "pos.customer.report"
    _description = "POS Customer Report"
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
    session_ids = fields.Many2many("pos.session", string="Sessions")
    user_ids = fields.Many2many("res.users", string="Cashiers")
    product_ids = fields.Many2many("product.product", string="Products")
    category_ids = fields.Many2many("product.category", string="Categories")
    pricelist_ids = fields.Many2many("product.pricelist", string="Pricelists")
    state_filter = fields.Selection([
        ('draft', 'Draft'),
        ('paid', 'Paid'),
        ('invoiced', 'Invoiced'),
        ('done', 'Done'),
        ('cancel', 'Cancelled'),
    ], string="Order Status")

    # ==== Summary Fields ====
    total_orders = fields.Integer(string="Total Orders", readonly=True)
    total_customers = fields.Integer(string="Total Customers", readonly=True)
    total_quantity = fields.Float(string="Total Quantity", readonly=True)
    total_discount = fields.Float(string="Total Discount", readonly=True)
    total_sales = fields.Float(string="Total Sales", readonly=True)
    total_tax = fields.Float(string="Total Tax", readonly=True)
    total_subtotal = fields.Float(string="Total Subtotal", readonly=True)

    # ==== Report Lines ====
    report_line_ids = fields.One2many("pos.customer.report.line", "report_id", string="Report Lines", readonly=True)

    # ==== Export Fields ====
    export_file = fields.Binary(readonly=True)
    export_filename = fields.Char()

    @api.depends('start_date', 'end_date')
    def _compute_name(self):
        for record in self:
            if record.start_date and record.end_date:
                record.name = f"Customer Report {record.start_date} to {record.end_date}"
            else:
                record.name = "New Customer Report"

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for record in self:
            if record.start_date > record.end_date:
                raise UserError(_("Start date cannot be after end date."))

    def _build_where_clause(self):
        """Build WHERE clause for the SQL query"""
        where_clauses = [
            "po.date_order::date >= %s",
            "po.date_order::date <= %s"
        ]
        params = [self.start_date, self.end_date]

        if self.branch_ids:
            where_clauses.append("po.config_id IN %s")
            params.append(tuple(self.branch_ids.ids))
        if self.session_ids:
            where_clauses.append("po.session_id IN %s")
            params.append(tuple(self.session_ids.ids))
        if self.user_ids:
            where_clauses.append("po.user_id IN %s")
            params.append(tuple(self.user_ids.ids))
        if self.state_filter:
            where_clauses.append("po.state = %s")
            params.append(self.state_filter)
        if self.product_ids:
            where_clauses.append("pol.product_id IN %s")
            params.append(tuple(self.product_ids.ids))
        if self.category_ids:
            where_clauses.append("pt.categ_id IN %s")
            params.append(tuple(self.category_ids.ids))
        if self.pricelist_ids:
            where_clauses.append("po.pricelist_id IN %s")
            params.append(tuple(self.pricelist_ids.ids))

        return " AND ".join(where_clauses), params

    def action_generate_report(self):
        """Generate the complete report"""
        self.ensure_one()

        # Clear previous lines
        self.report_line_ids.unlink()

        where_clause, params = self._build_where_clause()

        # Main query to get report data with additional fields
        query = f"""
            SELECT 
                po.id as order_id,
                po.name as order_reference,
                po.date_order as order_date,
                rp.name as customer_name,
                COALESCE(rp.mobile, rp.phone, '') as contact,
                pc.name as branch_name,
                emp.name as employee_name,
                pcateg.name as category_name,
                pt.name->>'en_US' as product_name,  
                pricelist.name->>'en_US' as pricelist_name,
                pol.qty as quantity,
                pol.price_unit as unit_price,
                pt.list_price as list_price,
                (pt.list_price - pol.price_unit) * pol.qty as discount,
                pol.price_subtotal as subtotal_excl_tax,
                pol.price_subtotal_incl as subtotal_incl_tax,
                (pol.price_subtotal_incl - pol.price_subtotal) as tax_amount,
                pol.price_subtotal_incl as order_total,
                pol.discount as line_discount_percent,
                pt.default_code as product_code
            FROM pos_order po
            LEFT JOIN pos_order_line pol ON po.id = pol.order_id
            LEFT JOIN product_product pp ON pol.product_id = pp.id
            LEFT JOIN product_template pt ON pp.product_tmpl_id = pt.id
            LEFT JOIN product_category pcateg ON pt.categ_id = pcateg.id
            LEFT JOIN res_partner rp ON po.partner_id = rp.id
            LEFT JOIN pos_config pc ON po.config_id = pc.id
            LEFT JOIN res_users ru ON po.user_id = ru.id
            LEFT JOIN hr_employee emp ON po.employee_id = emp.id
            LEFT JOIN product_pricelist pricelist ON po.pricelist_id = pricelist.id
            WHERE {where_clause}
            ORDER BY po.date_order DESC, po.id
        """

        self.env.cr.execute(query, tuple(params))
        results = self.env.cr.dictfetchall()

        if not results:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Data Found'),
                    'message': _('No sales data found for the selected period and filters.'),
                    'type': 'warning',
                    'sticky': True,
                }
            }

        # Calculate totals and create lines
        total_orders = len(set(row['order_id'] for row in results))
        total_customers = len(set(row['customer_name'] for row in results if row['customer_name']))
        total_quantity = sum(row['quantity'] or 0 for row in results)
        total_discount = sum(row['discount'] or 0 for row in results)
        total_subtotal = sum(row['subtotal_excl_tax'] or 0 for row in results)
        total_tax = sum(row['tax_amount'] or 0 for row in results)
        total_sales = sum(row['order_total'] or 0 for row in results)

        report_lines = []
        for row in results:
            line_vals = {
                'report_id': self.id,
                'order_reference': row['order_reference'],
                'order_date': row['order_date'],
                'customer_name': row['customer_name'] or 'Walk-in Customer',
                'contact': row['contact'],
                'branch_name': row['branch_name'],
                'employee_name': row['employee_name'],
                'category_name': row['category_name'],
                'product_name': row['product_name'],
                'product_code': row['product_code'],
                'pricelist_name': row['pricelist_name'],
                'quantity': row['quantity'] or 0,
                'unit_price': row['unit_price'] or 0,
                'list_price': row['list_price'] or 0,
                'discount': row['discount'] or 0,
                'line_discount_percent': row['line_discount_percent'] or 0,
                'subtotal_excl_tax': row['subtotal_excl_tax'] or 0,
                'subtotal_incl_tax': row['subtotal_incl_tax'] or 0,
                'tax_amount': row['tax_amount'] or 0,
                'order_total': row['order_total'] or 0,
            }
            report_lines.append((0, 0, line_vals))

        # Update report with new data
        self.write({
            'report_line_ids': report_lines,
            'total_orders': total_orders,
            'total_customers': total_customers,
            'total_quantity': total_quantity,
            'total_discount': total_discount,
            'total_subtotal': total_subtotal,
            'total_tax': total_tax,
            'total_sales': total_sales,
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

        # Write headers with additional fields
        headers = [
            'Order Reference', 'Order Date', 'Customer', 'Contact', 'Branch', 'Employee',
            'Category', 'Product', 'Product Code', 'Pricelist', 'Quantity',
            'Unit Price', 'List Price', 'Discount Amount', 'Discount %',
            'Subtotal (Excl Tax)', 'Subtotal (Incl Tax)', 'Tax Amount', 'Total'
        ]
        writer.writerow(headers)

        # Write data rows
        for line in self.report_line_ids:
            writer.writerow([
                line.order_reference or '',
                line.order_date.strftime('%Y-%m-%d %H:%M:%S') if line.order_date else '',
                line.customer_name,
                line.contact or '',
                line.branch_name or '',
                line.employee_name or '',
                line.category_name or '',
                line.product_name or '',
                line.product_code or '',
                line.pricelist_name or '',
                line.quantity,
                f"{line.unit_price:,.2f}",
                f"{line.list_price:,.2f}",
                f"{line.discount:,.2f}",
                f"{line.line_discount_percent:,.2f}%",
                f"{line.subtotal_excl_tax:,.2f}",
                f"{line.subtotal_incl_tax:,.2f}",
                f"{line.tax_amount:,.2f}",
                f"{line.order_total:,.2f}",
            ])

        # Write summary
        writer.writerow([])
        writer.writerow(['REPORT SUMMARY', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Orders', self.total_orders, '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Customers', self.total_customers, '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Quantity', self.total_quantity, '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Discount', f"{self.total_discount:,.2f}", '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Subtotal', f"{self.total_subtotal:,.2f}", '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Tax', f"{self.total_tax:,.2f}", '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''])
        writer.writerow(['Total Sales', f"{self.total_sales:,.2f}", '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''])

        # Prepare file for download
        csv_data = output.getvalue().encode('utf-8')
        b64_data = base64.b64encode(csv_data)

        filename = f"customer_report_{self.start_date}_to_{self.end_date}.csv"

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
            'session_ids': [(5, 0, 0)],
            'user_ids': [(5, 0, 0)],
            'product_ids': [(5, 0, 0)],
            'category_ids': [(5, 0, 0)],
            'pricelist_ids': [(5, 0, 0)],
            'state_filter': False,
            'total_orders': 0,
            'total_customers': 0,
            'total_quantity': 0,
            'total_discount': 0,
            'total_subtotal': 0,
            'total_tax': 0,
            'total_sales': 0,
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
            'name': _('Customer Report'),
            'res_model': 'pos.customer.report',
            'res_id': report.id,
            'view_mode': 'form',
            'target': 'current',
        }


class PosCustomerReportLine(models.Model):
    _name = "pos.customer.report.line"
    _description = "POS Customer Report Line"
    _order = "order_date desc"

    report_id = fields.Many2one("pos.customer.report", string="Report", required=True, ondelete="cascade")
    order_reference = fields.Char(string="Order Reference")
    order_date = fields.Datetime(string="Order Date")
    customer_name = fields.Char(string="Customer")
    contact = fields.Char(string="Contact")
    branch_name = fields.Char(string="Branch")
    employee_name = fields.Char(string="Employee")
    category_name = fields.Char(string="Category")
    product_name = fields.Char(string="Product")
    product_code = fields.Char(string="Product Code")
    pricelist_name = fields.Char(string="Pricelist")
    quantity = fields.Float(string="Quantity")
    unit_price = fields.Float(string="Unit Price")
    list_price = fields.Float(string="List Price")
    discount = fields.Float(string="Discount Amount")
    line_discount_percent = fields.Float(string="Discount %")
    subtotal_excl_tax = fields.Float(string="Subtotal (Excl Tax)")
    subtotal_incl_tax = fields.Float(string="Subtotal (Incl Tax)")
    tax_amount = fields.Float(string="Tax Amount")
    order_total = fields.Float(string="Total")