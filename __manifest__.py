# -*- coding: utf-8 -*-
{
    'name': "custom_addons/myreports",

    'summary': "Short (1 phrase/line) summary of the module's purpose",

    'description': """
Long description of module's purpose
    """,

    'author': "My Company",
    'website': "https://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Uncategorized',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'point_of_sale'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/pos_report.xml',  # ✅ Load first: contains action_pos_sales_report
        'views/menus.xml',  # ✅ Load second: uses the action
    ],

    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
}

