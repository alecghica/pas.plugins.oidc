# pylint: disable=C0111, C0301
# -*- coding: utf-8 -*-
"""Init and utils."""
from AccessControl.Permissions import manage_users as ManageUsers
from zope.i18nmessageid import MessageFactory
from Products.PluggableAuthService.PluggableAuthService import registerMultiPlugin  # noqa

_ = MessageFactory('pas.plugins.oidc')


def initialize(context):  # pragma: no cover
    """Initializer called when used as a Zope 2 product."""
    from pas.plugins.oidc import plugins

    registerMultiPlugin(plugins.OIDCPlugin.meta_type)

    context.registerClass(
        plugins.OIDCPlugin,
        permission=ManageUsers,
        constructors=(
            plugins.add_oidc_plugin, ),
        # icon='www/PluggableAuthService.png',
    )
