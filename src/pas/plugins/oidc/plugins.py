# pylint: disable=C0111, C0112, C0301, W0511, W1201, W0612
# -*- coding: utf-8 -*-
import itertools
import logging
import os
import string
from contextlib import contextmanager
from random import choice
from AccessControl import ClassSecurityInfo
from AccessControl.class_init import InitializeClass
from oic.oic import Client
from oic.oic.message import RegistrationResponse
from oic.utils.authn.client import CLIENT_AUTHN_METHOD
from plone.protect.utils import safeWrite
from Products.CMFCore.utils import getToolByName
from Products.CMFPlone.utils import safe_unicode
from Products.PluggableAuthService.interfaces.plugins import IAuthenticationPlugin  # noqa
from Products.PluggableAuthService.interfaces.plugins import IUserAdderPlugin
from Products.PluggableAuthService.plugins.BasePlugin import BasePlugin
from Products.PluggableAuthService.utils import classImplements
from ZODB.POSException import ConflictError
from zope.component.hooks import getSite
from zope.interface import Interface
from zope.interface import implementer


logger = logging.getLogger(__name__)
# _MARKER = object()
PWCHARS = string.ascii_letters + string.digits + string.punctuation
# LAST_UPDATE_USER_PROPERTY_KEY = 'last_autousermaker_update'


def context_property(name, default=None):

    def getter(self, name=name, default=default):
        SITE_STRING = '_' + getSite().getId()
        env_var = "OIDC" + name.upper() + SITE_STRING
        env_value = os.environ.get(env_var, default)

        if env_value == default:
            env_value = os.environ.get("OIDC" + name.upper(), default)

        if env_value == default:
            return env_value

        if isinstance(default, bool):
            if env_value.lower() == "true":
                return True

            if env_value.lower() == "false":
                return False

            return default

        if isinstance(default, tuple):
            if ',' in env_value:
                env_value = tuple("".join(env_value.split()).split(','))
            else:
                if env_value == "":
                    return ()

                env_value = (env_value, )

        if env_value == "":
            return getattr(self, name)

        return env_value

    def setter(self, value, name=name):
        setattr(self, name, value)

    def deleter(self, name=name):
        delattr(self, name)

    return property(getter, setter, deleter)


class IOIDCPlugin(Interface):
    """ """


@implementer(IOIDCPlugin)
class OIDCPlugin(BasePlugin):
    """PAS Plugin OpenID Connect.
    """

    meta_type = 'OIDC Plugin'
    security = ClassSecurityInfo()

    _issuer = ''
    _client_id = ''
    _client_secret = ''
    _redirect_uris = ()
    _use_session_data_manager = False
    _create_ticket = True
    _create_restapi_ticket = False
    _create_user = True
    _scope = ('profile', 'email', 'phone')
    _use_pkce = False
    _use_modified_openid_schema = False

    issuer = context_property('_issuer', '')
    client_id = context_property('_client_id', '')
    client_secret = context_property('_client_secret', '')
    redirect_uris = context_property('_redirect_uris', ())
    use_session_data_manager = context_property('_use_session_data_manager', False)  # noqa
    create_ticket = context_property('_create_ticket', True)
    create_restapi_ticket = context_property('_create_restapi_ticket', True)
    create_user = context_property('_create_user', True)
    scope = context_property('_scope', ('profile', 'email', 'phone'))
    use_pkce = context_property('_use_pkce', True)
    use_modified_openid_schema = context_property('_use_modified_openid_schema', False)  # noqa

    _properties = (
        dict(id='issuer', type='string', mode='w',
             label='OIDC/Oauth2 Issuer'),
        dict(id='client_id', type='string', mode='w',
             label='Client ID'),
        dict(id='client_secret', type='string', mode='w',
             label='Client secret'),
        dict(id='redirect_uris', type='lines', mode='w',
             label='Redirect uris'),
        dict(id='use_session_data_manager', type='boolean', mode='w',
             label='Use Zope session data manager.'),
        dict(id='create_user', type='boolean', mode='w',
             label='Create user / update user properties'),
        dict(id='create_ticket', type='boolean', mode='w',
             label='Create authentication __ac ticket. '),
        dict(id='create_restapi_ticket', type='boolean', mode='w',
             label='Create authentication auth_token (volto/restapi) ticket.'),
        dict(id='scope', type='lines', mode='w',
             label='Open ID scopes to request to the server'),
        dict(id='use_pkce', type='boolean', mode='w',
             label='Use PKCE. '),
        dict(id='use_modified_openid_schema', type='boolean', mode='w',
             label="Use a modified OpenID Schema for email_verified and phone_number_verified boolean values coming as string. "),  # noqa


    )

    def rememberIdentity(self, userinfo):
        # TODO: configurare mapping
        user_id = userinfo['preferred_username']
        pas = self._getPAS()
        if pas is None:
            return
        user = pas.getUserById(user_id)
        if self._create_user:
            # https://github.com/collective/Products.AutoUserMakerPASPlugin/blob/master/Products/AutoUserMakerPASPlugin/auth.py#L110
            if user is None:
                with safe_write(self.REQUEST):
                    userAdders = self.plugins.listPlugins(IUserAdderPlugin)
                    if not userAdders:
                        raise NotImplementedError("I wanted to make a new user, but"  # noqa
                                                " there are no PAS plugins active"  # noqa
                                                " that can make users.")

                    # Add the user to the first IUserAdderPlugin that works:
                    user = None
                    for _, curAdder in userAdders:
                        if curAdder.doAddUser(user_id, self._generatePassword()):  # noqa
                            # Assign a dummy password. It'll never be used;.
                            user = self._getPAS().getUser(user_id)
                            try:
                                membershipTool = getToolByName(self, 'portal_membership')  # noqa
                                if not membershipTool.getHomeFolder(user_id):
                                    membershipTool.createMemberArea(user_id)
                            except (ConflictError, KeyboardInterrupt):
                                raise
                            except Exception:
                                pass
                            self._updateUserProperties(user, userinfo)
                            break
            else:
                with safe_write(self.REQUEST):
                    self._updateUserProperties(user, userinfo)
        if user and self._create_ticket:
            self._setupTicket(user_id)
        if user and self._create_restapi_ticket:
            self._setupJWTTicket(user_id, user)

    def _updateUserProperties(self, user, userinfo):
        """ Update the given user properties from the set of credentials.
        This is utilised when first creating a user, and to update
        their information when logging in again later.
        """
        userProps = {}
        if 'email' in userinfo:
            userProps['email'] = userinfo['email']
        if 'given_name' in userinfo and 'family_name' in userinfo:
            userProps['fullname'] = '{} {}'.format(userinfo['given_name'], userinfo['family_name'])  # noqa
        elif 'name' in userinfo and 'family_name' in userinfo:
            userProps['fullname'] = '{} {}'.format(userinfo['name'], userinfo['family_name'])  # noqa
        # userProps[LAST_UPDATE_USER_PROPERTY_KEY] = time.time()
        if userProps:
            user.setProperties(**userProps)

    def _generatePassword(self):
        """ Return a obfuscated password never used for login """
        return ''.join([choice(PWCHARS) for ii in range(40)])

    def _setupTicket(self, user_id):
        """Set up authentication ticket (__ac cookie) with plone.session.

        Only call this when self._create_ticket is True.
        """
        pas = self._getPAS()
        if pas is None:
            return
        if 'session' not in pas:
            return
        info = pas._verifyUser(pas.plugins, user_id=user_id)
        if info is None:
            logger.debug('No user found matching header. Will not set up session.')  # noqa
            return
        request = self.REQUEST
        response = request['RESPONSE']
        pas.session._setupSession(user_id, response)
        logger.debug('Done setting up session/ticket for %s' % user_id)

    def _setupJWTTicket(self, user_id, user):
        """Set up JWT authentication ticket (auth_token cookie).

        Only call this when self._create_restapi_ticket is True.
        """
        authenticators = self.plugins.listPlugins(IAuthenticationPlugin)
        plugin = None
        for id_, authenticator in authenticators:
            if authenticator.meta_type == "JWT Authentication Plugin":
                plugin = authenticator
                break
        if plugin:
            payload = {}
            payload["fullname"] = user.getProperty("fullname")
            token = plugin.create_token(user.getId(), data=payload)
            request = self.REQUEST
            response = request['RESPONSE']
            # TODO: take care of path, cookiename and domain options ?
            response.setCookie('auth_token', token, path='/')

    # TODO: memoize (?)
    def get_oauth2_client(self):
        client = Client(client_authn_method=CLIENT_AUTHN_METHOD)
        # registration_response = client.register(
        #           provider_info["registration_endpoint"], redirect_uris=...)
        # ... oic.exception.RegistrationError: {'error': 'insufficient_scope',
        #     'error_description': "Policy 'Trusted Hosts' rejected request
        #     to client-registration service. Details: Host not trusted."}

        # use WebFinger
        provider_info = client.provider_config(self.issuer)
        logger.info("Provider info:")
        logger.info(provider_info)

        info = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
        }
        client_reg = RegistrationResponse(**info)
        client.store_registration_info(client_reg)
        return client

    def get_redirect_uris(self):
        if self.redirect_uris:
            return [safe_unicode(u) for u in self.redirect_uris]

        return ['{}/callback'.format(self.absolute_url()), ]

    def get_scopes(self):
        if self.scope:
            return [safe_unicode(u) for u in self.scope]
        return []


InitializeClass(OIDCPlugin)

classImplements(
    OIDCPlugin,
    IOIDCPlugin,
    # IExtractionPlugin,
    # IAuthenticationPlugin,
    # IChallengePlugin,
    # IPropertiesPlugin,
    # IRolesPlugin,
)


def add_oidc_plugin():
    # Form for manually adding our plugin.
    # But we do this in setuphandlers.py always.
    pass


# https://github.com/collective/Products.AutoUserMakerPASPlugin/blob/master/Products/AutoUserMakerPASPlugin/auth.py
@contextmanager
def safe_write(request):
    """Disable CSRF protection of plone.protect for a block of code.
    Inside the context manager objects can be written to without any
    restriction. The context manager collects all touched objects
    and marks them as safe write."""
    objects_before = set(_registered_objects(request))
    yield
    objects_after = set(_registered_objects(request))
    for obj in objects_after - objects_before:
        safeWrite(obj, request)


def _registered_objects(request):
    """Collect all objects part of a pending write transaction."""
    app = request.PARENTS[-1]
    return list(itertools.chain.from_iterable([conn._registered_objects
             # skip the 'temporary' connection since it stores session objects
             # which get written all the time
                for (name, conn) in app._p_jar.connections.items() if name != 'temporary']))  # noqa
