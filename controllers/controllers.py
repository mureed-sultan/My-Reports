# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import io
import csv

class PosSalesReportController(http.Controller):

    @http.route('/pos_sales_report/download_csv/<int:wizard_id>', type='http', auth='user')
    def download_pos_sales_report_csv(self, wizard_id, **kwargs):
        wizard = request.env['pos.sales.report.wizard'].browse(wizard_id)
        if not wizard.exists():
            return request.not_found()

        # Prepare CSV data
        output = io.StringIO()
        writer = csv.writer(output)
        headers = [
            'Employee Name', 'Employee Barcode', 'Order ID', 'Order Reference', 'Order Date',
            'Customer Name', 'POS Config', 'Session', 'Cashier Login', 'Pricelist',
            'Product', 'Category', 'Quantity Sold', 'Original Price Unit', 'Actual Price Unit',
            'Unit Discount', 'Total Before Discount', 'Subtotal Excl Tax',
            'Subtotal Incl Tax', 'Tax Value', 'Order Total', 'Order State'
        ]
        writer.writerow(headers)

        for line in wizard.line_ids:
            writer.writerow([
                line.employee_name or '',
                line.employee_barcode or '',
                line.order_id or '',
                line.order_reference or '',
                line.order_date or '',
                line.customer_name or '',
                line.pos_config_name or '',
                line.session_name or '',
                line.cashier_login or '',
                line.pricelist_name or '',
                line.product_name or '',
                line.category_name or '',
                line.quantity_sold or 0,
                line.original_price_unit or 0,
                line.actual_price_unit or 0,
                line.unit_discount or 0,
                line.total_before_discount or 0,
                line.subtotal_excl_tax or 0,
                line.subtotal_incl_tax or 0,
                line.tax_value or 0,
                line.order_total or 0,
                line.order_state or '',
            ])

        csv_content = output.getvalue()
        output.close()

        # Create HTTP response
        filename = f"POS_Sales_Report_{wizard.start_date}_to_{wizard.end_date}.csv"
        response = request.make_response(
            csv_content,
            headers=[
                ('Content-Type', 'text/csv;charset=utf-8'),
                ('Content-Disposition', f'attachment; filename="{filename}"'),
            ]
        )
        return response
