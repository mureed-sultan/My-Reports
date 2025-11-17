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
    html_table = fields.Html("Report", readonly=True, sanitize=False)

    def _build_where_clause(self):
        """Build WHERE clause for the SQL query"""
        where_clauses = [
            "po.date_order >= %s",
            "po.date_order <= %s"
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
        if self.state:
            where_clauses.append("po.state = %s")
            params.append(self.state)
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

    def _execute_sql_query(self, query, params):
        """Execute raw SQL query and return results"""
        self.env.cr.execute(query, params)
        return self.env.cr.dictfetchall()

    def action_fetch_report(self):
        """Generate HTML table with customer sales data using raw SQL"""
        where_clause, params = self._build_where_clause()

        query = f"""
            SELECT 
                po.date_order as order_date,
                rp.name as customer_name,
                COALESCE(rp.mobile, rp.phone, '') as contact,
                pc.name as branch_name,
                emp.name as employee_name,
                pcateg.name as category_name,
                pt.name as product_name,
                pricelist.name as pricelist_name,
                (pt.list_price - pol.price_unit) * pol.qty as discount,
                pol.price_subtotal_incl as order_value
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

        results = self._execute_sql_query(query, params)

        # Calculate totals
        total_discount = sum(row['discount'] or 0 for row in results)
        total_value = sum(row['order_value'] or 0 for row in results)

        # Generate HTML table
        table_rows = []
        for row in results:
            table_rows.append(f"""
                <tr>
                    <td>{row['order_date'].strftime('%Y-%m-%d') if row['order_date'] else ''}</td>
                    <td>{row['customer_name'] or ''}</td>
                    <td>{row['contact'] or ''}</td>
                    <td>{row['branch_name'] or ''}</td>
                    <td>{row['employee_name'] or ''}</td>
                    <td>{row['category_name'] or ''}</td>
                    <td>{row['product_name'] or ''}</td>
                    <td>{row['pricelist_name'] or ''}</td>
                    <td style="text-align:right;">{row['discount'] or 0:.2f}</td>
                    <td style="text-align:right;">{row['order_value'] or 0:.2f}</td>
                </tr>
            """)

        table_html = f"""
            <div style="margin-bottom:10px; padding:10px; background:#f8f9fa; border-radius:5px;">
                <strong>Total Discount:</strong> {total_discount:.2f} |
                <strong>Total Order Value:</strong> {total_value:.2f} |
                <strong>Total Records:</strong> {len(results)}
            </div>
            <table class="table table-sm table-hover table-bordered" style="width:100%;">
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
                    {''.join(table_rows)}
                </tbody>
            </table>
        """

        # Update ONLY the current record without creating new ones
        self.write({'html_table': table_html})

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_generate_csv(self):
        """Generate CSV export using raw SQL"""
        where_clause, params = self._build_where_clause()

        query = f"""
            SELECT 
                po.date_order as order_date,
                rp.name as customer_name,
                COALESCE(rp.mobile, rp.phone, '') as contact,
                pc.name as branch_name,
                emp.name as employee_name,
                pcateg.name as category_name,
                pt.name as product_name,
                pricelist.name as pricelist_name,
                (pt.list_price - pol.price_unit) * pol.qty as discount,
                pol.price_subtotal_incl as order_value
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

        results = self._execute_sql_query(query, params)

        # Generate CSV in memory
        output = io.BytesIO()
        writer = csv.writer(io.TextIOWrapper(output, encoding='utf-8', write_through=True))

        # Write header
        writer.writerow([
            "Order Date", "Customer", "Contact", "Branch", "Employee",
            "Category", "Product", "Pricelist", "Discount", "Order Value"
        ])

        # Write data rows
        total_discount = 0.0
        total_value = 0.0

        for row in results:
            discount = row['discount'] or 0
            value = row['order_value'] or 0
            total_discount += discount
            total_value += value

            writer.writerow([
                row['order_date'].strftime('%Y-%m-%d') if row['order_date'] else '',
                row['customer_name'] or '',
                row['contact'] or '',
                row['branch_name'] or '',
                row['employee_name'] or '',
                row['category_name'] or '',
                row['product_name'] or '',
                row['pricelist_name'] or '',
                f"{discount:.2f}",
                f"{value:.2f}",
            ])

        # Add totals row
        writer.writerow([])
        writer.writerow(["", "", "", "", "", "", "", "TOTAL", f"{total_discount:.2f}", f"{total_value:.2f}"])

        # Get CSV data
        output.seek(0)
        csv_data = output.read()
        output.close()

        # Create a temporary attachment (this is cleaned up automatically)
        attachment = self.env['ir.attachment'].create({
            'name': f"Customer_Report_{self.start_date}_{self.end_date}.csv",
            'type': 'binary',
            'datas': base64.b64encode(csv_data),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'text/csv'
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f"/web/content/{attachment.id}?download=true",
            'target': 'self',
        }