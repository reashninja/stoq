# #3289: Remove is_valid_model from domain classes where it's unused

def apply_patch(trans):
    for view_name in ['client_view', 'service_view']:
        if trans.viewExists(view_name):
            trans.dropView(view_name)

    for table_name in ['address',
                       'bank',
                       'bank_account',
                       'base_sellable_info',
                       'bill_check_group_data',
                       'branch_station',
                       'calls',
                       'card_installment_settings',
                       'cfop_data',
                       'check_data',
                       'city_location',
                       'commission',
                       'commission_source',
                       'credit_provider_group_data',
                       'delivery_item',
                       'device_settings',
                       'employee_role',
                       'employee_role_history',
                       'fiscal_day_history',
                       'fiscal_day_tax',
                       'gift_certificate_type',
                       'inheritable_model',
                       'inheritable_model_adapter',
                       'installed_plugin',
                       'liaison',
                       'military_data',
                       'on_sale_info',
                       'parameter_data',
                       'payment',
                       'payment_adapt_to_in_payment',
                       'payment_adapt_to_out_payment',
                       'payment_method',
                       'payment_operation',
                       'person',
                       'person_adapt_to_bank_branch',
                       'person_adapt_to_branch',
                       'person_adapt_to_client',
                       'person_adapt_to_company',
                       'person_adapt_to_credit_provider',
                       'person_adapt_to_employee',
                       'person_adapt_to_individual',
                       'person_adapt_to_sales_person',
                       'person_adapt_to_supplier',
                       'person_adapt_to_transporter',
                       'person_adapt_to_user',
                       'po_adapt_to_payment_deposit',
                       'po_adapt_to_payment_devolution',
                       'product',
                       'product_adapt_to_storable',
                       'product_history',
                       'product_retention_history',
                       'product_stock_item',
                       'product_stock_reference',
                       'product_supplier_info',
                       'profile_settings',
                       'purchase_item',
                       'receiving_order_item',
                       'renegotiation_data',
                       'sellable_category',
                       'sellable_tax_constant',
                       'sellable_unit',
                       'service',
                       'service_sellable_item_adapt_to_delivery',
                       'till',
                       'till_entry',
                       'user_profile',
                       'voter_data',
                       'work_permit_data',
                       ]:
        if not trans.tableHasColumn(table_name, 'is_valid_model'):
            continue
        trans.query('ALTER TABLE %s DROP COLUMN is_valid_model' % (
            table_name, ))
    trans.commit()