# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2006-2011 Async Open Source <http://www.async.com.br>
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
##
"""First time installation wizard for Stoq

Stoq Configuration dialogs

Current flow of the database steps:

  * :obj:`WelcomeStep`
  * :obj:`DatabaseLocationStep`

    * :obj:`DatabaseSettingsStep` (iff network database)

      * :obj:`PostgresAdminPasswordStep`
      * :obj:`FinishInstallationStep` (if an installed database was found)

  * :obj:`InstallationModeStep`
  * :obj:`PluginStep`

    * :obj:`TefStep` (if tef was activated)

  * :obj:`StoqAdminPasswordStep`
  * :obj:`CreateDatabaseStep`
  * :obj:`FinishInstallationStep`
"""

import logging
import os
import platform

import glib
import gtk
from kiwi.component import provide_utility
from kiwi.datatypes import ValidationError
from kiwi.python import Settable
from kiwi.ui.delegates import GladeSlaveDelegate

from stoqlib.api import api
from stoqlib.exceptions import DatabaseInconsistency
from stoqlib.database.admin import (USER_ADMIN_DEFAULT_NAME, ensure_admin_user,
                                    create_default_profile_settings)
from stoqlib.database.interfaces import (ICurrentBranchStation,
                                         ICurrentBranch)
from stoqlib.database.settings import (DatabaseSettings, test_local_database,
                                       validate_database_name)
from stoqlib.domain.person import LoginUser
from stoqlib.domain.station import BranchStation
from stoqlib.domain.system import SystemTable
from stoqlib.exceptions import DatabaseError
from stoqlib.gui.base.wizards import (BaseWizard, WizardEditorStep,
                                      WizardStep)
from stoqlib.gui.slaves.userslave import PasswordEditorSlave
from stoqlib.gui.utils.logo import render_logo_pixbuf
from stoqlib.gui.widgets.processview import ProcessView
from stoqlib.lib.configparser import StoqConfig
from stoqlib.lib.formatters import raw_phone_number
from stoqlib.lib.kiwilibrary import library
from stoqlib.lib.message import error, warning, yesno
from stoqlib.lib.osutils import get_product_key, read_registry_key
from stoqlib.lib.validators import validate_email
from stoqlib.lib.webservice import WebService
from stoqlib.lib.translation import stoqlib_gettext as _
from stoqlib.net.socketutils import get_hostname
from twisted.internet import reactor

from stoq.gui.shell.shell import PRIVACY_STRING
from stoq.lib.options import get_option_parser
from stoq.lib.startup import setup

logger = logging.getLogger(__name__)


LOGO_WIDTH = 91
LOGO_HEIGHT = 32

#
# Wizard Steps
#

(TRUST_AUTHENTICATION,
 PASSWORD_AUTHENTICATION) = range(2)


class BaseWizardStep(WizardStep, GladeSlaveDelegate):
    """A wizard step base class definition"""
    gladefile = None

    def __init__(self, wizard, previous=None):
        logger.info('Entering step: %s' % self.__class__.__name__)
        self.wizard = wizard
        WizardStep.__init__(self, previous)
        GladeSlaveDelegate.__init__(self, gladefile=self.gladefile)


class WelcomeStep(BaseWizardStep):
    gladefile = "WelcomeStep"

    def __init__(self, wizard):
        BaseWizardStep.__init__(self, wizard)
        self._update_widgets()

    def _update_widgets(self):
        self.image1.set_from_pixbuf(render_logo_pixbuf('config'))
        self.title_label.set_bold(True)

    def next_step(self):
        return DatabaseLocationStep(self.wizard, self)


class DatabaseLocationStep(BaseWizardStep):
    gladefile = 'DatabaseLocationStep'

    def post_init(self):
        self.radio_local.grab_focus()

    def next_step(self):
        self.wizard.db_is_local = self.radio_local.get_active()
        # If we're not connecting to a local, ask for the
        # connection settings
        if not self.wizard.db_is_local:
            return DatabaseSettingsStep(self.wizard, self)

        settings = self.wizard.settings
        # FIXME: Allow developers to specify another database
        #        is_developer_mode() or STOQ_DATABASE_NAME
        settings.dbname = "stoq"
        if platform.system() == 'Windows':
            settings.address = "localhost"
            settings.username = "postgres"
            settings.port = self.wizard.get_win32_postgres_port()
            return PostgresAdminPasswordStep(self.wizard, self)
        else:
            settings.address = ""  # Unix socket really

        return self.wizard.connect_for_settings(self)

    def on_radio_local__activate(self, radio):
        self.wizard.go_to_next()

    def on_radio_network__activate(self, radio):
        self.wizard.go_to_next()


class DatabaseSettingsStep(WizardEditorStep):
    gladefile = 'DatabaseSettingsStep'
    model_type = DatabaseSettings
    proxy_widgets = ('address',
                     'port',
                     'username',
                     'password',
                     'dbname')

    def __init__(self, wizard, previous, focus_dbname=True):
        self.focus_dbname = focus_dbname
        WizardEditorStep.__init__(self, None, wizard, wizard.settings,
                                  previous)
        self._update_widgets()

    def _update_widgets(self):
        selected = self.authentication_type.get_selected_data()
        need_password = selected == PASSWORD_AUTHENTICATION
        self.password.set_sensitive(need_password)
        self.passwd_label.set_sensitive(need_password)

    #
    # WizardStep hooks
    #

    def post_init(self):
        self.register_validate_function(self.wizard.refresh_next)
        self.force_validation()
        if self.focus_dbname:
            self.dbname.grab_focus()
        else:
            self.address.grab_focus()

    def validate_step(self):
        if not self.model.check_database_address():
            msg = _("The database address '%s' is invalid. "
                    "Please fix it and try again") % self.model.address
            warning(_(u'Invalid database address'), msg)
            # '' is not strictly invalid, since it's an alias for
            # unix socket, so don't tell that to the user, make him
            # belive that he still uses "localhost"
            if self.model.address != "":
                self.address.set_invalid(_("Invalid database address"))
                self.force_validation()
            return False

        self.wizard.write_pgpass()
        settings = self.wizard.settings

        # If we configured setting to localhost, try connecting
        # with address == '', eg unix socket first before trying
        # to connect to localhost. This is done because the default
        # postgres configuration doesn't allow you to connect via localhost,
        # only unix socket.
        if settings.address == 'localhost':
            if not self.wizard.try_connect(settings, warn=False):
                settings.address = ''

        if not self.wizard.try_connect(settings):
            # Restore it
            settings.address = 'localhost'
            return False

        if settings.address == '':
            # Reload settings as they changed
            self.wizard.config.load_settings(settings)

        self.wizard.auth_type = self.authentication_type.get_selected()

        return True

    def setup_proxies(self):
        self.authentication_type.prefill([
            (_("Needs Password"), PASSWORD_AUTHENTICATION),
            (_("Trust"), TRUST_AUTHENTICATION)])

        self.add_proxy(self.model, DatabaseSettingsStep.proxy_widgets)
        # Show localhost instead of empty for unix socket, not strictly
        # correct but better than showing nothing.
        if not self.model.address:
            self.address.set_text("localhost")
        self.model.stoq_user_data = Settable(password='')
        self.add_proxy(self.model.stoq_user_data)

    def next_step(self):
        if self.wizard.has_installed_db:
            return FinishInstallationStep(self.wizard)
        elif self.wizard.check_incomplete_database():
            self.dbname.grab_focus()
            return self
        else:
            return InstallationModeStep(self.wizard, self)

    #
    # Callbacks
    #

    def on_authentication_type__content_changed(self, *args):
        self._update_widgets()

    def on_dbname__validate(self, widget, value):
        if not validate_database_name(value):
            return ValidationError(_('%s is not a valid database name') % value)


class InstallationModeStep(BaseWizardStep):
    gladefile = "InstallationModeStep"
    model_type = object

    def post_init(self):
        self.empty_database_radio.grab_focus()

    def next_step(self):
        self.wizard.enable_production = not self.empty_database_radio.get_active()
        return PluginStep(self.wizard, self)

    def on_empty_database_radio__activate(self, radio):
        self.wizard.go_to_next()

    def on_example_database_radio__activate(self, radio):
        self.wizard.go_to_next()


class PluginStep(BaseWizardStep):
    gladefile = 'PluginStep'

    def post_init(self):
        self.wizard.plugins = []
        self.enable_ecf.grab_focus()
        if platform.system() == 'Windows':
            self.enable_ecf.set_active(False)
            self.enable_ecf.set_sensitive(False)
            self.enable_tef.set_active(False)
            self.enable_tef.set_sensitive(False)

    def next_step(self):
        if self.enable_ecf.get_active():
            self.wizard.plugins.append('ecf')
        if self.enable_nfe.get_active():
            self.wizard.plugins.append('nfe')

        if self.enable_tef.get_active() and not self.wizard.tef_request_done:
            return TefStep(self.wizard, self)
        return StoqAdminPasswordStep(self.wizard, self)


class TefStep(WizardEditorStep):
    """Since we are going to sell the TEF funcionality, we cant enable the
    plugin right away. Just ask for some user information and we will
    contact.
    """
    gladefile = 'TefStep'
    model_type = Settable
    proxy_widgets = ('name', 'email', 'phone')

    def __init__(self, wizard, previous):
        model = Settable(name='', email='', phone='')
        WizardEditorStep.__init__(self, None, wizard, model, previous)
        self._setup_widgets()

    #
    #   Private API
    #

    def _setup_widgets(self):
        self.send_progress.hide()
        self.send_error_label.hide()

    def _pulse(self):
        # FIXME: This is a hack, remove it when we can avoid
        #        calling dialog.run()
        reactor.doIteration(0.1)
        reactor.runUntilCurrent()

        self.send_progress.pulse()
        return not self.wizard.tef_request_done

    def _cancel_request(self):
        if not self.wizard.tef_request_done:
            self._show_error()
        return False

    def _show_error(self):
        self.wizard.tef_request_done = True
        self.send_progress.hide()
        self.send_error_label.show()
        self.wizard.next_button.set_sensitive(True)

    #
    #   WizardStep
    #

    def post_init(self):
        self.register_validate_function(self.wizard.refresh_next)
        self.force_validation()
        self.name.grab_focus()

    def setup_proxies(self):
        self.add_proxy(self.model, TefStep.proxy_widgets)

    def next_step(self):
        # We already sent the details, but may still be on the same step.
        if self.wizard.tef_request_done:
            return StoqAdminPasswordStep(self.wizard, self.previous)

        webapi = WebService()
        response = webapi.tef_request(self.model.name, self.model.email,
                                      self.model.phone)
        response.addCallback(self._on_response_done)
        response.addErrback(self._on_response_error)

        # FIXME: This is a hack, remove it when we can avoid
        #        calling dialog.run()
        if not reactor.running:
            reactor.run()

        self.send_progress.show()
        self.send_progress.set_text(_('Sending...'))
        self.send_progress.set_pulse_step(0.05)
        self.details_table.set_sensitive(False)
        self.wizard.next_button.set_sensitive(False)
        glib.timeout_add(50, self._pulse)

        # Cancel the request after 30 seconds without a reply
        glib.timeout_add(30000, self._cancel_request)

        # Stay on the same step while sending the details
        return self

    #
    #   Callbacks
    #

    def on_email__validate(self, widget, value):
        if not validate_email(value):
            return ValidationError(_('%s is not a valid email') % value)

    def on_phone__validate(self, widget, value):
        if len(raw_phone_number(value)) != 10:
            return ValidationError(_('%s is not a valid phone') % value)

    def on_phone__activate(self, widget):
        if self.wizard.next_button.get_sensitive():
            self.wizard.go_to_next()

    def _on_response_done(self, details):
        if details['response'] != 'success':
            self._show_error()
            return

        if not self.wizard.tef_request_done:
            self.wizard.tef_request_done = True
            self.wizard.go_to_next()

    def _on_response_error(self, err):
        self._show_error()


class PasswordStep(BaseWizardStep):
    gladefile = 'AdminPasswordStep'

    def __init__(self, wizard, previous):
        BaseWizardStep.__init__(self, wizard, previous)
        self.description_label.set_markup(
            self.get_description_label())
        self.title_label.set_markup(self.get_title_label())
        self.setup_slaves()

    def get_slave(self):
        return PasswordEditorSlave(None)

    #
    # WizardStep hooks
    #

    def setup_slaves(self):
        self.password_slave = self.get_slave()
        self.attach_slave("password_holder", self.password_slave)

    def post_init(self):
        self.register_validate_function(self.wizard.refresh_next)
        self.force_validation()
        self.password_slave.password.grab_focus()


class PostgresAdminPasswordStep(PasswordStep):
    """ Ask a password for posgres administration user. """
    title_label = _("PostgresSQL 'postgres' password")

    def get_title_label(self):
        return '<b>%s</b>' % _("PostgreSQL password")

    def get_description_label(self):
        return _("To be able to create a new Stoq database you need to enter "
                 "the password of the <b>postgres</b> user for the PostgreSQL "
                 "database we're installing to.\n\n"
                 "This is the same password you were asked when you installed "
                 "PostgreSQL on this computer.")

    def get_slave(self):
        return PasswordEditorSlave(None, confirm_password=False)

    #
    # WizardStep hooks
    #

    def next_step(self):
        self.wizard.settings.password = str(self.password_slave.model.new_password)
        self.wizard.auth_type = PASSWORD_AUTHENTICATION
        self.wizard.write_pgpass()
        return self.wizard.connect_for_settings(self)


class StoqAdminPasswordStep(PasswordStep):
    """ Ask a password for the new user being created. """
    title_label = _("Administrator account")

    def get_title_label(self):
        return '<b>%s</b>' % _("Administrator account")

    def get_description_label(self):
        return _("I'm adding a user account called <b>%s</b> which will "
                 "have administrator privilegies.\n\nTo be "
                 "able to create other users you need to login "
                 "with this user in the admin application and "
                 "create them.") % USER_ADMIN_DEFAULT_NAME

    #
    # WizardStep hooks
    #

    def validate_step(self):
        # FIXME: self.password_slave os not implementing any
        #        validate_confirm, so, this check is useless.
        good_pass = self.password_slave.validate_confirm()
        if good_pass:
            self.wizard.options.login_username = 'admin'
            self.wizard.login_password = self.password_slave.model.new_password
        return good_pass

    def next_step(self):
        if self.wizard.db_is_local and not test_local_database():
            return InstallPostgresStep(self.wizard, self)
        else:
            return CreateDatabaseStep(self.wizard, self)


class InstallPostgresStep(BaseWizardStep):
    """Since we are going to sell the TEF funcionality, we cant enable the
    plugin right away. Just ask for some user information and we will
    contact.
    """
    gladefile = 'InstallPostgresStep'

    def __init__(self, wizard, previous):
        self.done = False
        BaseWizardStep.__init__(self, wizard, previous)
        self._setup_widgets()

    def _setup_widgets(self):
        forward_label = '<b>%s</b>' % (_("Forward"), )

        if self._can_install():
            self.description.props.label += (
                "\n\n" +
                _("The installation guide will now install the packages for you "
                  "using apt, it may ask you for your password to continue."))

            # Translators: %s is the string "Forward"
            label = _("Click %s to begin installing the "
                      "PostgreSQL server.") % (
                forward_label, )
        else:
            # Translators: %s is the string "Forward"
            label = _("Click %s to continue when you have installed "
                      "PostgreSQL server on this computer.") % (
                forward_label, )
        self.label.set_markup(label)

    def _can_install(self):
        try:
            import aptdaemon
            aptdaemon  # pylint: disable=W0104
            return True
        except ImportError:
            return False

    def _install_postgres(self):
        from stoqlib.gui.utils.aptpackageinstaller import AptPackageInstaller
        self.wizard.disable_back()
        self.wizard.disable_next()
        apti = AptPackageInstaller(parent=self.wizard.get_toplevel())
        apti.install('postgresql', 'postgresql-contrib')
        apti.connect('done', self._on_apt_install__done)
        apti.connect('auth-failed', self._on_apt_install__auth_failed)

        self.label.set_markup(
            _("Please wait while the package installation is completing."))

    #
    #   WizardStep
    #
    def next_step(self):
        if self.done or test_local_database():
            return CreateDatabaseStep(self.wizard, self)

        if self._can_install():
            self._install_postgres()
        else:
            warning(_("You need to install PostgreSQL before moving forward"))

        return self

    #
    #   Callbacks
    #

    def _on_apt_install__done(self, apt, err):
        if error is not None:
            warning(_("Something went wrong while trying to install "
                      "the PostgreSQL server."))
            self.label.set_markup(
                _("Sorry, something went wrong while installing PostgreSQL, "
                  "try again manually or go back and configure Stoq to connect "
                  "to another."))
            self.wizard.enable_back()
        else:
            self.done = True
            # FIXME: Update label and enable_back/next instead
            #        of doing it automatically for the user,
            #        to tell him that postgres installation succeeded.
            self.wizard.go_to_next()

    def _on_apt_install__auth_failed(self, apt):
        self.wizard.enable_back()
        self.wizard.enable_next()
        self.label.set_markup(
            _("Authorization failed, try again or connect to "
              "another database"))


class CreateDatabaseStep(BaseWizardStep):
    gladefile = 'CreateDatabaseStep'

    def post_init(self):
        self.n_patches = 0
        self.process_view = ProcessView()
        self.process_view.listen_stderr = True
        self.process_view.connect('read-line', self._on_processview__readline)
        self.process_view.connect('finished', self._on_processview__finished)
        self.expander.add(self.process_view)
        self.expander.grab_focus()
        self._maybe_create_database()

    def next_step(self):
        return FinishInstallationStep(self.wizard)

    def _maybe_create_database(self):
        logger.info('_maybe_create_database (db_is_local=%s, remove_demo=%s)'
                    % (self.wizard.db_is_local, self.wizard.remove_demo))
        if self.wizard.db_is_local:
            self._launch_stoqdbadmin()
            return
        elif self.wizard.remove_demo:
            self._launch_stoqdbadmin()
            return

        self.wizard.write_pgpass()
        settings = self.wizard.settings
        self.wizard.config.load_settings(settings)

        # store = settings.get_super_store()
        # version = store.dbVersion()
        # if version < (8, 1):
        #     info(_("Stoq requires PostgresSQL 8.1 or later, but %s found") %
        #          ".".join(map(str, version)))
        #     store.close()
        #     return False

        # Secondly, ask the user if he really wants to create the database,
        dbname = settings.dbname
        if yesno(_(u"The specified database '%s' does not exist.\n"
                   u"Do you want to create it?") % dbname,
                 gtk.RESPONSE_YES, _(u"Create database"), _(u"Don't create")):
            self.process_view.feed("** Creating database\r\n")
            self._launch_stoqdbadmin()
        else:
            self.process_view.feed("** Not creating database\r\n")
            self.wizard.disable_next()

    def _launch_stoqdbadmin(self):
        logger.info('_launch_stoqdbadmin')
        self.wizard.disable_back()
        self.wizard.disable_next()
        stoqdbadmin = 'stoqdbadmin'
        if platform.system() == 'Windows':
            if library.uninstalled:
                stoqdbadmin += '.bat'
            else:
                stoqdbadmin += '.exe'
            # FIXME: listen to file input for
            #        APPDATA/stoqdbadmin/stderr.log + stdout.log
        args = [stoqdbadmin, 'init',
                '--no-load-config',
                '--no-register-station',
                '-v']
        if self.wizard.enable_production and not self.wizard.remove_demo:
            args.append('--demo')
        if self.wizard.plugins:
            args.append('--enable-plugins')
            args.append(','.join(self.wizard.plugins))
        if self.wizard.db_is_local:
            args.append('--create-dbuser')

        dbargs = self.wizard.settings.get_command_line_arguments()
        args.extend(dbargs)
        self.label.set_label(
            _("Creating a new database for Stoq, depending on the speed of "
              "your computer and the server it may take a couple of "
              "minutes to finish."))
        self.progressbar.set_text(_("Creating database..."))
        self.progressbar.set_fraction(0.05)
        logger.info(' '.join(args))
        self.process_view.execute_command(args)
        self.done_label.set_markup(
            _("Please wait while the database is being created."))

    def _parse_process_line(self, line):
        LOG_CATEGORY = 'stoqlib.database.create'
        log_pos = line.find(LOG_CATEGORY)
        if log_pos == -1:
            return
        line = line[log_pos + len(LOG_CATEGORY) + 1:]
        if line == 'SCHEMA':
            value = 0.1
            text = _("Creating base schema...")
        elif line.startswith('PATCHES:'):
            value = 0.35
            self.n_patches = int(line.split(':', 1)[1])
            text = _("Creating schema, applying patches...")
        elif line.startswith('PATCH:'):
            # 0.4 - 0.7 patches
            patch = float(line.split(':', 1)[1])
            value = 0.4 + (patch / self.n_patches) * 0.3
            text = _("Creating schema, applying patch %d ...") % (patch + 1, )
        elif line == 'INIT START':
            text = _("Creating additional database objects ...")
            value = 0.8
        elif line == 'INIT DONE' and self.wizard.enable_production:
            text = _("Creating examples ...")
            value = 0.85
        elif line.startswith('PLUGIN'):
            text = _("Activating plugins ...")
            if 'nfe' in self.wizard.plugins:
                text += ' ' + _('This may take some time.')
            value = 0.95
        else:
            return
        self.progressbar.set_fraction(value)
        self.progressbar.set_text(text)

    def _finish(self, returncode):
        logger.info('CreateDatabaseStep._finish (returncode=%s)' % returncode)
        if returncode:
            self.wizard.enable_back()
            # Failed to execute/create database
            if returncode == 30:
                # This probably happened because the user either;
                # - pressed cancel in the authentication popup
                # - user erred the password 3 times
                # Allow him to try again
                if yesno(_("Something went wrong while trying to create "
                           "the database. Try again?"),
                         gtk.RESPONSE_NO, _("Change settings"), _("Try again")):
                    return
                self._launch_stoqdbadmin()
                return
            else:
                # Unknown error, just inform user that something went wrong.
                self.expander.set_expanded(True)
                warning(_("Something went wrong while trying to create "
                          "the Stoq database"))
            return
        self.label.set_text("")
        self.wizard.load_config_and_call_setup()
        create_default_profile_settings()
        ensure_admin_user(self.wizard.config.get_password())
        self.progressbar.set_text(_("Done."))
        self.progressbar.set_fraction(1.0)
        self.wizard.enable_next()
        self.done_label.set_markup(
            _("Installation successful, click <b>Forward</b> to continue."))

    # Callbacks

    def _on_processview__readline(self, view, line):
        self._parse_process_line(line)

    def _on_processview__finished(self, view, returncode):
        self._finish(returncode)


class FinishInstallationStep(BaseWizardStep):
    gladefile = 'FinishInstallationStep'

    def has_next_step(self):
        return False

    def post_init(self):
        # replaces the cancel button with a quit button
        self.wizard.cancel_button.set_label(gtk.STOCK_QUIT)
        # self._cancel will be a callback for the quit button
        self.wizard.cancel = self._cancel
        self.wizard.next_button.set_label(_(u'Run Stoq'))
        self.online_services.set_active(self.wizard.enable_online_services)

        self.wizard.next_button.grab_focus()

        if self.wizard.has_installed_db:
            self.online_services.hide()
            self.online_info.hide()

        # If you have a ProductKey you are required to enable online
        # services
        if get_product_key():
            self.online_services.hide()
            self.online_info.hide()
            self.online_services.set_active(True)

    def _cancel(self):
        # This is the last step, so we will finish the installation
        # before we quit
        self.wizard.finish(run=False)

    def on_online_services__toggled(self, check):
        self.wizard.enable_online_services = check.get_active()

    def on_online_info__clicked(self, button):
        dialog = gtk.MessageDialog(parent=None, flags=0,
                                   type=gtk.MESSAGE_INFO,
                                   buttons=gtk.BUTTONS_OK,
                                   message_format=_("Online services"))
        dialog.format_secondary_markup(PRIVACY_STRING)
        dialog.run()
        dialog.destroy()

#
# Main wizard
#


class FirstTimeConfigWizard(BaseWizard):
    title = _("Stoq - Installation")
    size = (580, 380)
    tef_request_done = False

    def __init__(self, options, config=None):
        if not config:
            config = StoqConfig()
        self.settings = config.get_settings()

        self.enable_production = False
        self.config = config
        self.remove_demo = False
        self.has_installed_db = False
        self.options = options
        self.plugins = []
        self.db_is_local = False
        self.enable_online_services = True
        self.auth_type = TRUST_AUTHENTICATION

        if config.get('Database', 'enable_production') == 'True':
            self.remove_demo = True

        if self.remove_demo:
            first_step = PluginStep(self)
        else:
            first_step = WelcomeStep(self)
        BaseWizard.__init__(self, None, first_step, title=self.title)

        self.get_toplevel().set_deletable(False)
        self.next_button.grab_focus()

    def _create_station(self, store):
        # FIXME: This is fishy, we can probably simplify this significantly by
        #        allowing users to connect to the initial database without
        #        having a branch station nor branch registered.
        #        The whole BranchStation/Branch creation is weird, it should
        #        be done at the same place.
        logger.info('_create_station')
        if self.enable_production:
            branch = api.sysparam.get_object(store, 'MAIN_COMPANY')
            assert branch
            provide_utility(ICurrentBranch, branch)
        else:
            branch = None

        station_name = get_hostname()
        if store.find(BranchStation, branch=branch, name=station_name).one():
            return
        station = BranchStation(store=store,
                                is_active=True,
                                branch=branch,
                                name=station_name)
        provide_utility(ICurrentBranchStation, station)

    def _set_admin_password(self, store):
        logger.info('_set_admin_password')
        adminuser = store.find(LoginUser,
                               username=USER_ADMIN_DEFAULT_NAME).one()
        if adminuser is None:
            raise DatabaseInconsistency(
                ("You should have a user with username: %s"
                 % USER_ADMIN_DEFAULT_NAME))
        adminuser.set_password(self.login_password)

    def _set_online_services(self, store):
        logger.info('_set_online_services (%s)' %
                    self.enable_online_services)
        api.sysparam.set_bool(store, 'ONLINE_SERVICES', self.enable_online_services)

    # Public API

    def get_win32_postgres_port(self):
        for v in ['9.2', '9.1', '9.0', '8.4']:
            key = read_registry_key(
                'HKLM', r'Software\PostgreSQL\Services\postgresql-%s' % (v, ),
                'Port')
            if key:
                return key

        # Default port
        return 5432

    def try_connect(self, settings, warn=True):
        logger.info('try_connect (warn=%s)' % (warn))
        logger.info('settings: address=%s username=%s, dbname=%s' % (
            settings.address, settings.username, settings.dbname))
        self.config.load_settings(settings)
        try:
            if settings.has_database():
                store = settings.create_store()
                self.has_installed_db = SystemTable.is_available(store)
                store.close()
        except DatabaseError as e:
            if warn:
                warning(e.short, str(e.msg))
            logger.info('Failed to connect')
            return False

        logger.info('Connected')
        return True

    def check_incomplete_database(self):
        logger.info('check_incomplete_database (db_is_local=%s)' %
                   (self.db_is_local, ))
        # If we don't have postgres installed we cannot have
        # an incomplete database
        if self.db_is_local and not test_local_database():
            return False

        # a database which doesn't exist isn't incomplete
        try:
            if not self.settings.has_database():
                return False
        except DatabaseError as e:
            # If we're install stoq locally and hasn't created a database
            # user yet, we'll receive an authentiction error, there's no
            # way to reliably check for this, so assume all errors are
            # authentication errors
            # first install on 9.1: FATAL: role "stoq" does not exist.
            if self.db_is_local:
                return False
            msg = (_('It was not possible to connect to the database.') +
                   '\n' + _('Check the server configuration and try again.'))
            warning(msg, str(e))
            return True

        # If we have the SystemTable we are pretty much there,
        # could verify a few more tables in the future, including
        # row content of the tables.
        store = self.settings.create_store()
        if SystemTable.is_available(store):
            store.close()
            return False
        store.close()

        # okay, we have a database which exists and doesn't have
        # the "SystemTable" SQL table present, means that we cannot use
        # it and should warn the user

        # Not 100% correct, should perhaps say "unix socket"
        address = self.settings.address or "localhost"
        msg = _("Database {dbname} at {address}:{port} is not "
                "a Stoq database.").format(
            dbname=self.settings.dbname,
            address=address,
            port=self.settings.port)
        description = _(
            "Stoq was able to succesfully connect to the database "
            "{dbname} at the database server {address}, however it "
            "is not a Stoq database or it was corrupted, please select "
            "another one.").format(dbname=self.settings.dbname,
                                   address=self.settings.address or "localhost")
        warning(msg, description)
        return True

    def load_config_and_call_setup(self):
        dbargs = self.settings.get_command_line_arguments()
        parser = get_option_parser()
        db_options, unused_args = parser.parse_args(dbargs)
        self.config.set_from_options(db_options)
        try:
            setup(self.config,
                  options=self.options,
                  check_schema=True,
                  register_station=False,
                  load_plugins=False)
        except DatabaseInconsistency as err:
            error(_('The database version differs from your installed '
                    'version.'), str(err))

    def connect_for_settings(self, step):
        # Try to connect, we don't care if we can connect,
        # we just want to know if it's properly installed
        self.try_connect(self.settings, warn=False)

        # Corrupted or a non-Stoq database
        if self.check_incomplete_database():
            self.settings.dbname = ""
            return DatabaseSettingsStep(self, step, focus_dbname=True)

        if self.has_installed_db:
            return FinishInstallationStep(self)

        return InstallationModeStep(self, step)

    def write_pgpass(self):
        # Only save password if using password authentication
        if self.auth_type != PASSWORD_AUTHENTICATION:
            return

        logger.info('write_pgpass')
        # There's no way to pass in the password to psql via the command line,
        # so we need to setup a pgpass where we store the password entered here
        if platform.system() == 'Windows':
            directory = os.path.join(os.environ['APPDATA'],
                                     'postgresql')
            passfile = os.path.join(directory, 'pgpass.conf')
        else:
            directory = os.environ.get('HOME', '/')
            passfile = os.path.join(directory, '.pgpass')
        pgpass = os.environ.get('PGPASSFILE', passfile)

        # FIXME: remove duplicates when trying several different passwords
        if os.path.exists(pgpass):
            lines = []
            for line in open(pgpass):
                if line[-1] == '\n':
                    line = line[:-1]
                lines.append(line)
        else:
            lines = []

        # _get_store_internal connects to the 'postgres' database,
        # we need to allow connections without asking the password,
        # otherwise stoqdbadmin will be stuck in the wizard when creating
        # a new database.
        for dbname in ['postgres', self.settings.dbname]:
            line = '%s:%s:%s:%s:%s' % (self.settings.address,
                                       self.settings.port,
                                       dbname,
                                       self.settings.username,
                                       self.settings.password)
            if line in lines:
                continue
            lines.append(line)

        if not os.path.exists(directory):
            os.makedirs(directory)
        fd = open(pgpass, 'w')
        fd.write('\n'.join(lines))
        fd.write('\n')
        fd.close()
        os.chmod(pgpass, 0600)

    #
    # WizardStep hooks
    #

    def cancel(self, *args):
        raise SystemExit

    def finish(self, run=True):
        if self.has_installed_db:
            self.load_config_and_call_setup()
        else:
            # Commit data created during the wizard, such as stations
            store = api.new_store()
            self._set_admin_password(store)
            self._create_station(store)
            self._set_online_services(store)
            store.commit()

        # Write configuration to disk
        if self.remove_demo:
            self.config.remove('Database', 'enable_production')
        self.config.flush()

        self.close()

        # This wizard is shown with run_dialog, that creates a new main loop,
        # but we may have created another main loop when calling reactor.run()
        # above. Make sure to leave only one main loop running after the wizard.
        if reactor.running:
            reactor.prepare_restart()
