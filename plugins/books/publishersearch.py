# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2010 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU Lesser General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s): Stoq Team <stoq-devel@async.com.br>
##

""" Search dialog/Editor for publishers """

from kiwi.argcheck import argcheck
from kiwi.enums import SearchFilterPosition
from kiwi.ui.search import ComboSearchFilter
from kiwi.ui.objectlist import Column, SearchColumn

from stoqlib.database.runtime import new_transaction
from stoqlib.lib.translation import stoqlib_gettext
from stoqlib.lib.formatters import format_phone_number

from stoqlib.gui.base.search import SearchEditor, SearchDialog
from stoqlib.gui.base.dialogs import run_dialog
from stoqlib.gui.search.personsearch import BasePersonSearch
from stoqlib.gui.templates.persontemplate import BasePersonRoleEditor

from booksdomain import PersonAdaptToPublisher, PublisherView
from booksinterfaces import IPublisher

_ = stoqlib_gettext


class PublisherEditor(BasePersonRoleEditor):
    model_name = _(u'Publisher')
    title = _(u'New Publisher')
    model_iface = IPublisher
    gladefile = 'BaseTemplate'

    def create_model(self, conn):
        person = BasePersonRoleEditor.create_model(self, conn)
        publisher = IPublisher(person, None)
        if publisher is None:
            publisher = person.addFacet(IPublisher, connection=conn)
        return publisher


class PublisherSearch(BasePersonSearch):
    title = _('Publisher Search')
    editor_class = PublisherEditor
    table = PublisherView
    size = (750, 450)
    search_lbl_text = _('Publishers matching:')
    result_strings = _('publisher'), _('publishers')

    def _get_status_values(self):
        items = [(value, key) for key, value in
                 PersonAdaptToPublisher.statuses.items()]
        items.insert(0, (_('Any'), None))
        return items

    #
    # SearchDialog Hooks
    #

    def create_filters(self):
        self.set_text_field_columns(['name'])

    def get_columns(self):
        return [SearchColumn('name', _('Name'), str, width=250, expand=True),]

    def get_editor_model(self, model):
        return model.publisher
