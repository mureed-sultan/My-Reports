# -*- coding: utf-8 -*-
{
    'name': "POS Sales Report",
    'summary': "Generate detailed sales and staff performance reports for POS.",
    'description': """
POS Sales Report
================
This module provides advanced reporting for Point of Sale (POS), including
customer sales, staff performance, and service tracking.
    """,
    'author': "iStallana Solutions",
    'website': "https://www.yourcompany.com",
    'category': 'Point of Sale',
    'version': '0.1',

    # Dependencies
    'depends': ['base', 'point_of_sale'],

    # Data files loaded always
    'data': [
        'security/ir.model.access.csv',
        'views/pos_commission_report.xml',
        'views/pos_customer_report.xml',
        'views/staff_service_performance_report_wizard_view.xml',
        # 'views/customer_report_views.xml',
        # 'views/pos_sales_report.xml',
        'views/menus.xml',
    ],

    # Demo dataValueError: No record found for unique ID base.public_user. It may have been deleted.

    'demo': ['demo/demo.xml'],

    # Technical flags
    'installable': True,
    'application': True,
    'auto_install': False,
}
