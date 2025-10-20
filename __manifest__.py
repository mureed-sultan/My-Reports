# -*- coding: utf-8 -*-
{
    'name': "POS Sales Report",

    'summary': "Short (1 phrase/line) summary of the module's purpose",

    'description': """
Long description of module's purpose
    """,

    'author': "iStallana Solutions",
    'website': "https://www.yourcompany.com",
    'category': 'Uncategorized',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'point_of_sale'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/pos_report.xml',
        'views/customer_report_views.xml',
        'views/pos_sales_report.xml',
        'views/menus.xml',
    ],

    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
}

