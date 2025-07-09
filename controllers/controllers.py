# -*- coding: utf-8 -*-
# from odoo import http


# class CustomAddons/myreports(http.Controller):
#     @http.route('/custom_addons/myreports/custom_addons/myreports', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/custom_addons/myreports/custom_addons/myreports/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('custom_addons/myreports.listing', {
#             'root': '/custom_addons/myreports/custom_addons/myreports',
#             'objects': http.request.env['custom_addons/myreports.custom_addons/myreports'].search([]),
#         })

#     @http.route('/custom_addons/myreports/custom_addons/myreports/objects/<model("custom_addons/myreports.custom_addons/myreports"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('custom_addons/myreports.object', {
#             'object': obj
#         })

