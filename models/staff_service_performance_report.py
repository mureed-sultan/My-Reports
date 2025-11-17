# models/pos_sales_report_page.py
from odoo import models, fields, api
from odoo.exceptions import UserError
import csv
from io import StringIO
import base64

class PosSalesReportPage(models.TransientModel):
    _name = "pos.sales.report.page"
    _description = "POS Sales Report Page"

    start_date = fields.Datetime(string="Start Date", required=True)
    end_date = fields.Datetime(string="End Date", required=True)
    branch_id = fields.Many2one('pos.config', string="Branch")
    employee_id = fields.Many2one('hr.employee', string="Employee")
    report_html = fields.Html(string="Report", readonly=True)
    report_file = fields.Binary(string="CSV Report", readonly=True)
    report_filename = fields.Char(string="File Name")

    def _fetch_query_results(self):
        query = """
            SELECT
                he.id AS employee_id,
                he.name AS employee_name,
                he.barcode AS employee_batch_no,
                (dj.name ->> 'en_US') AS job_position,
                (dep.name ->> 'en_US') AS department_name,
                he.work_email,
                he.work_phone,
                he.identification_id AS employee_national_id,
                po.id AS order_id,
                po.name AS order_name,
                po.pos_reference,
                po.amount_total AS order_total,
                po.amount_tax AS order_tax,
                po.amount_paid AS order_paid,
                po.amount_return AS order_return,
                po.amount_total - po.amount_tax AS order_untaxed,
                po.date_order AT TIME ZONE 'UTC' AS order_date,
                ps.name AS session_name,
                pc.name AS pos_branch,
                (ppm.name ->> 'en_US') AS payment_method,
                ppay.amount AS payment_amount,
                po.partner_id,
                rp.name AS customer_name,
                rp.phone AS customer_phone,
                rp.mobile AS customer_mobile,
                rp.email AS customer_email,
                pol.id AS line_id,
                (pt.name ->> 'en_US') AS product_name,
                pcateg.name AS product_category,
                pt.type AS product_type,
                pt.default_code AS product_internal_code,
                pol.qty AS quantity,
                pol.price_unit AS unit_price,
                (pol.qty * pol.price_unit) AS line_total,
                uom.name AS uom_name,
                SUM(pol.qty * pol.price_unit) OVER (PARTITION BY he.id) AS employee_total_sale
            FROM pos_order po
            LEFT JOIN hr_employee he ON po.employee_id = he.id
            LEFT JOIN hr_department dep ON he.department_id = dep.id
            LEFT JOIN hr_job dj ON he.job_id = dj.id
            LEFT JOIN pos_session ps ON po.session_id = ps.id
            LEFT JOIN pos_config pc ON ps.config_id = pc.id
            LEFT JOIN pos_order_line pol ON pol.order_id = po.id
            LEFT JOIN product_product pp ON pol.product_id = pp.id
            LEFT JOIN product_template pt ON pp.product_tmpl_id = pt.id
            LEFT JOIN uom_uom uom ON pt.uom_id = uom.id
            LEFT JOIN product_category pcateg ON pt.categ_id = pcateg.id
            LEFT JOIN res_partner rp ON po.partner_id = rp.id
            LEFT JOIN pos_payment ppay ON ppay.pos_order_id = po.id
            LEFT JOIN pos_payment_method ppm ON ppay.payment_method_id = ppm.id
            WHERE po.state = 'done'
              AND po.date_order >= %s
              AND po.date_order <= %s
        """
        params = [self.start_date, self.end_date]

        if self.branch_id:
            query += " AND pc.id = %s"
            params.append(self.branch_id.id)
        if self.employee_id:
            query += " AND he.id = %s"
            params.append(self.employee_id.id)

        query += " ORDER BY he.name ASC, po.date_order ASC"
        self.env.cr.execute(query, params)
        return self.env.cr.dictfetchall()

    def generate_report(self):
        self.ensure_one()
        results = self._fetch_query_results()
        if not results:
            self.report_html = "<p>No results found for the selected filters.</p>"
            return

        html = "<table border='1' cellpadding='4' cellspacing='0'>"
        headers = list(results[0].keys())
        html += "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
        for row in results:
            html += "<tr>" + "".join(f"<td>{row[h]}</td>" for h in headers) + "</tr>"
        html += "</table>"
        self.report_html = html

    def download_csv(self):
        self.ensure_one()
        results = self._fetch_query_results()
        if not results:
            raise UserError("No data available for CSV download.")
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
        self.report_file = base64.b64encode(output.getvalue().encode())
        self.report_filename = f"pos_sales_report_{self.start_date.date()}_to_{self.end_date.date()}.csv"
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/?model=pos.sales.report.page&id={self.id}&field=report_file&filename={self.report_filename}&download=true",
            "target": "self",
        }