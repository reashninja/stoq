# -*- Mode: Python; coding: iso-8859-1 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2004 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307,
## USA.
##
"""
stoq/gui/slaves/individual.py

    Individual edition template slaves implementation.
"""


from stoqlib.gui.editors import BaseEditorSlave

from stoq.lib.parameters import get_system_parameter
from stoq.domain.person import CityLocation, PersonAdaptToIndividual
from stoq.lib.runtime import new_transaction


class IndividualDocuments(BaseEditorSlave):
    model_type = PersonAdaptToIndividual
    gladefile = 'IndividualDocuments'

    widgets = ('cpf',
               'rg_expedition_date',
               'rg_expedition_local',
               'rg_number')

    def __init__(self, conn, model):
        BaseEditorSlave.__init__(self, conn, model)

    def setup_proxies(self):
        self.proxy = self.add_proxy(self.model, self.widgets)

class IndividualDetailsSlave(BaseEditorSlave):
    model_type = PersonAdaptToIndividual
    gladefile = 'IndividualDetailsSlave'

    birth_loc_widgets = ('birth_loc_city',
                         'birth_loc_country',
                         'birth_loc_state')

    proxy_widgets = ('birth_date',
                     'mother_name',
                     'father_name',
                     'occupation',
                     # spouse, # XXX: depends on bug #2001 to work
                     'marital_status',
                     'gender')

    widgets = (proxy_widgets + birth_loc_widgets + 
               ('new_spouse_button', 'spouse', 'spouse_lbl'))

    def __init__(self, conn, model):
        BaseEditorSlave.__init__(self, conn, model)

    def setup_entries_completion(self):
        sparam = get_system_parameter(self.conn)
        cities = [sparam.CITY_SUGGESTED]
        states = sparam.CITY_LOCATION_STATES
        countries = [sparam.COUNTRY_SUGGESTED]

        for city_loc in CityLocation.select(connection=self.conn):
            if city_loc.city and city_loc.city not in cities:
                cities.append(city_loc.city)
            if city_loc.country and city_loc.country not in countries:
                countries.append(city_loc.country)
            if city_loc.state and city_loc.state not in states:
                states.append(city_loc.state)

        self.birth_loc_city.set_completion_strings(cities)
        self.birth_loc_country.set_completion_strings(countries)
        self.birth_loc_state.set_completion_strings(states)

    def setup_combos(self):
        genders = self.model.genders
        items = [(genders[k], k) for k in genders.keys()]
        self.gender.prefill(items, sort=True)

        self.marital_status.prefill(self.model.get_marital_statuses())

        # XXX: spouse combo won't be filled until we get the proper support.
        # see bug #2001
        self.spouse.clear()

    def ensure_city_location(self):
        """ Search for the birth location fields in database, if found some
        item, reuse the city location object """
        birthloc = self.model.birth_location

        if not birthloc.is_valid_model():
            self.model.birth_location = None
            CityLocation.delete(birthloc.id, connection=self.conn)
            return

        query = ("city = '%s' and state = '%s' and country = '%s'"
                 % (birthloc.city, birthloc.state, birthloc.country))
        conn = new_transaction()
        result = CityLocation.select(query, connection=conn)
        conn.close()

        if not result.count():
            return

        msg = ("You should get just one city_location instance. Probably you "
               "have a database inconsistency.")
        assert result.count() == 1, msg

        self.model.birth_location = CityLocation.get(result[0].id)
        CityLocation.delete(birthloc.id, connection=self.conn)

    #
    # Kiwi handlers
    #

    def on_marital_status__changed(self, *args):
        marital_status = self.marital_status.get_selected_data()
        visible = (marital_status == PersonAdaptToIndividual.STATUS_MARRIED)

        widgets = (self.new_spouse_button, self.spouse, self.spouse_lbl)
        for widget in widgets:
            widget.set_property('visible', visible)

    def on_new_spouse_button__clicked(self, *args):
        # XXX: Waiting fix for bug #2001
        #spouse = run_dialog(IndividualEditor, None, self.conn)
        #self.model.spouse = spouse
        pass

    #
    # BaseEditorSlave hooks
    #

    def setup_proxies(self):
        self.setup_entries_completion()
        self.setup_combos()

        self.proxy = self.add_proxy(self.model, self.proxy_widgets)

        if self.model.birth_location:
            self.model.birth_location = self.model.birth_location.clone()
        else:
            c = CityLocation(connection=self.conn)
            self.model.birth_location = c

        self.birth_loc_proxy = self.add_proxy(self.model.birth_location,
                                              self.birth_loc_widgets)

    def on_confirm(self):
        self.ensure_city_location()
        return self.model



