"""
Twitter OAuth Support for Google App Engine Apps.

Using this in your app should be relatively straightforward:

* Edit the configuration section below with the CONSUMER_KEY and CONSUMER_SECRET
  from Twitter.

* Modify to reflect your App's domain and set the callback URL on Twitter to:

    http://your-app-name.appspot.com/oauth/twitter/callback

* Use the demo in ``MainHandler`` as a starting guide to implementing your app.

Note: You need to be running at least version 1.1.9 of the App Engine SDK.

-- 
I hope you find this useful, tav

"""

# Released into the Public Domain by tav@espians.com

import sys
import logging

from datetime import datetime, timedelta
from hashlib import sha1
from hmac import new as hmac
from os.path import dirname, join as join_path
from random import getrandbits
from time import time
from urllib import urlencode, quote as urlquote
from uuid import uuid4
from wsgiref.handlers import CGIHandler

sys.path.insert(0, join_path(dirname(__file__), 'lib')) # extend sys.path

from demjson import decode as decode_json
from flash import Flash

from google.appengine.api.urlfetch import fetch as urlfetch, GET, POST, DownloadError
from google.appengine.ext import db
from google.appengine.ext.webapp import RequestHandler, WSGIApplication
from google.appengine.api.datastore_errors import Timeout

# ------------------------------------------------------------------------------
# configuration -- SET THESE TO SUIT YOUR APP!!
# ------------------------------------------------------------------------------

OAUTH_APP_SETTINGS = {

    'twitter': {

        'consumer_key': 'bbWMofaqB28rooL8sSCYw',
        'consumer_secret': 'sE6GfG23rexk8vY7T18W8gLd0Q9jhcQ9EiJzJihBak',

        'request_token_url': 'https://twitter.com/oauth/request_token',
        'access_token_url': 'https://twitter.com/oauth/access_token',
        'user_auth_url': 'http://twitter.com/oauth/authorize',

        'default_api_prefix': 'http://api.twitter.com/1',
        'default_api_suffix': '.json',

        },

    'google': {

        'consumer_key': '',
        'consumer_secret': '',

        'request_token_url': 'https://www.google.com/accounts/OAuthGetRequestToken',
        'access_token_url': 'https://www.google.com/accounts/OAuthGetAccessToken',
        'user_auth_url': 'https://www.google.com/accounts/OAuthAuthorizeToken',

        },

    }

CLEANUP_BATCH_SIZE = 100
EXPIRATION_WINDOW = timedelta(seconds=60*60*1) # 1 hour

try:
    from config import OAUTH_APP_SETTINGS
except:
    pass

STATIC_OAUTH_TIMESTAMP = 12345 # a workaround for clock skew/network lag

# ------------------------------------------------------------------------------
# utility functions
# ------------------------------------------------------------------------------

def get_service_key(service, cache={}):
    if service in cache: return cache[service]
    return cache.setdefault(
        service, "%s&" % encode(OAUTH_APP_SETTINGS[service]['consumer_secret'])
        )

def create_uuid():
    return 'id-%s' % uuid4()

def encode(text):
    return urlquote(str(text), safe='~')

def twitter_specifier_handler(client):
    return client.get('/account/verify_credentials')['screen_name']

OAUTH_APP_SETTINGS['twitter']['specifier_handler'] = twitter_specifier_handler

# ------------------------------------------------------------------------------
# db entities
# ------------------------------------------------------------------------------

class OAuthRequestToken(db.Model):
    """OAuth Request Token."""

    service = db.StringProperty()
    oauth_token = db.StringProperty()
    oauth_token_secret = db.StringProperty()
    created = db.DateTimeProperty(auto_now_add=True)

class OAuthAccessToken(db.Model):
    """OAuth Access Token."""

    service = db.StringProperty()
    specifier = db.StringProperty()
    oauth_token = db.StringProperty()
    oauth_token_secret = db.StringProperty()
    created = db.DateTimeProperty(auto_now_add=True)

# ------------------------------------------------------------------------------
# oauth client
# ------------------------------------------------------------------------------

class OAuthClient(object):

    __public__ = ('callback', 'cleanup', 'login', 'logout')

    def __init__(self, service, handler, oauth_callback=None, **request_params):
        self.service = service
        self.service_info = OAUTH_APP_SETTINGS[service]
        self.service_key = None
        self.handler = handler
        self.request_params = request_params
        self.oauth_callback = oauth_callback
        self.token = None

    # public methods

    def token_for_user(self, user_name):
      count = 0
      token = None
      while count < 3:
        try:
          tokens = OAuthAccessToken.all().filter(
                  'specifier =', user_name).filter(
                  'service =', 'twitter').fetch(1)
          if len(tokens) > 0:
            token = tokens[0]
          else:
            logging.warning("Could not find token for user %s" % user_name)
          break
        except Timeout, e:
          logging.warning("Timedout(updateStatuswithToken): Trying again")
          count += 1

      if token == None:
        logging.error("Could not fetch client token in 3 attempts. Giving up")
      return token


    def get(self, api_method, http_method='GET', expected_status=(200,), tuser = None, **extra_params):

        if not (api_method.startswith('http://') or api_method.startswith('https://')):
            api_method = '%s%s%s' % (
                self.service_info['default_api_prefix'], api_method,
                self.service_info['default_api_suffix']
                )

        request_headers = {}

        if tuser:
          if tuser.accessTokenid:
            self.token = self.token_for_user(tuser.user) # TODO Check return value
            if self.token == None:
              raise ValueError("Could not get token for %s with phone %s" % (tuser.user,tuser.phonenumber))
            request_url = self.get_signed_url(api_method, self.token, http_method, **extra_params)
          else:
            request_headers['Authorization'] = 'Basic %s' % tuser.basic_auth
            request_url = self.get_unsigned_url(api_method, http_method, **extra_params)
        else:
          if self.token is None:
            self.token = OAuthAccessToken.get_by_key_name(self.get_cookie())
          request_url = self.get_signed_url(api_method, self.token, http_method, **extra_params)

        fetch = urlfetch(request_url, headers = request_headers, deadline = 10)

        if fetch.status_code not in expected_status:
            raise ValueError(
                "Error calling... Got return status: %i [%r]" %
                (fetch.status_code, fetch.content)
                )

        return decode_json(fetch.content)

    def post(self, api_method, http_method='POST', expected_status=(200,), tuser = None, **extra_params):

        if not (api_method.startswith('http://') or api_method.startswith('https://')):
            api_method = '%s%s%s' % (
                self.service_info['default_api_prefix'], api_method,
                self.service_info['default_api_suffix']
                )

        request_headers = {}
        if tuser:
          if tuser.accessTokenid:
            self.token = self.token_for_user(tuser.user) # TODO Check return value
            if self.token == None:
              raise ValueError("Could not get token for %s with phone %s" % (tuser.user,tuser.phonenumber))
            request_data = self.get_signed_body(api_method, self.token, http_method, **extra_params)
          else:
            request_headers['Authorization'] = 'Basic %s' % tuser.basic_auth
            request_data = self.get_unsigned_body(api_method, http_method, **extra_params)
        else:
          if self.token is None:
            self.token = OAuthAccessToken.get_by_key_name(self.get_cookie())
          request_data = self.get_signed_body(api_method, self.token, http_method, **extra_params)


        fetch = urlfetch(url=api_method, headers=request_headers, payload=request_data, method=http_method, deadline = 10)

        if fetch.status_code not in expected_status:
            raise ValueError(
                "Error calling... Got return status: %i [%r]" %
                (fetch.status_code, fetch.content)
                )

        return decode_json(fetch.content)

    def login(self):

      try:
        proxy_id = self.get_cookie()

        if proxy_id:
            return "FOO%rFF" % proxy_id
            self.expire_cookie()

        return self.get_request_token()
      except (DownloadError, ValueError), e:
        logging.warning("Twitter/login: Failed. redirectin to the home page" )
        flash = Flash()
        flash.msg = "Twitter is not responding currently. Please try again in some time. Hopefully it will be up."
        self.redirect("/")


    def logout(self, return_to='/'):
        self.expire_cookie()
        self.handler.redirect(self.handler.request.get("return_to", return_to))

    # oauth workflow

    def get_request_token(self):

        token_info = self.get_data_from_signed_url(
            self.service_info['request_token_url'], **self.request_params
            )

        token = OAuthRequestToken(
            service=self.service,
            **dict(token.split('=') for token in token_info.split('&'))
            )

        token.put()

        if self.oauth_callback:
            oauth_callback = {'oauth_callback': self.oauth_callback}
        else:
            oauth_callback = {}

        self.handler.redirect(self.get_signed_url(
            self.service_info['user_auth_url'], token, **oauth_callback
            ))

    def callback(self, return_to='/'):

        oauth_token = self.handler.request.get("oauth_token")

        if not oauth_token:
            return self.get_request_token()

        oauth_token = OAuthRequestToken.all().filter(
            'oauth_token =', oauth_token).filter(
            'service =', self.service).fetch(1)[0]

        token_info = self.get_data_from_signed_url(
            self.service_info['access_token_url'], oauth_token
            )

        key_name = create_uuid()

        self.token = OAuthAccessToken(
            key_name=key_name, service=self.service,
            **dict(token.split('=') for token in token_info.split('&'))
            )

        # TODO : Handle the GET timeout error in the following line
        if 'specifier_handler' in self.service_info:
            specifier = self.token.specifier = self.service_info['specifier_handler'](self)
            old = OAuthAccessToken.all().filter(
                'specifier =', specifier).filter(
                'service =', self.service)
            db.delete(old)

        self.token.put()
        self.set_cookie(key_name)
        self.handler.redirect(return_to)

    def cleanup(self):
        query = OAuthRequestToken.all().filter(
            'created <', datetime.now() - EXPIRATION_WINDOW
            )
        count = query.count(CLEANUP_BATCH_SIZE)
        db.delete(query.fetch(CLEANUP_BATCH_SIZE))
        return "Cleaned %i entries" % count

    # request marshalling

    def get_data_from_signed_url(self, __url, __token=None, __meth='GET', **extra_params):
        return urlfetch(self.get_signed_url(
            __url, __token, __meth, **extra_params
            ), deadline = 10).content

    def get_unsigned_url(self, __url,  __meth='GET',**extra_params):
        return '%s?%s'%(__url, self.get_unsigned_body(__url, __meth, **extra_params))

    def get_unsigned_body(self, __url,  __meth='GET',**extra_params):
        kwargs = {}
        kwargs.update(extra_params)

        return urlencode(kwargs)

    def get_signed_url(self, __url, __token=None, __meth='GET',**extra_params):
        return '%s?%s'%(__url, self.get_signed_body(__url, __token, __meth, **extra_params))

    def get_signed_body(self, __url, __token=None, __meth='GET',**extra_params):

        service_info = self.service_info

        kwargs = {
            'oauth_consumer_key': service_info['consumer_key'],
            'oauth_signature_method': 'HMAC-SHA1',
            'oauth_version': '1.0',
            'oauth_timestamp': int(time()),
            'oauth_nonce': getrandbits(64),
            }

        kwargs.update(extra_params)

        if self.service_key is None:
            self.service_key = get_service_key(self.service)

        if __token is not None:
            kwargs['oauth_token'] = __token.oauth_token
            key = self.service_key + encode(__token.oauth_token_secret)
        else:
            key = self.service_key

        message = '&'.join(map(encode, [
            __meth.upper(), __url, '&'.join(
                '%s=%s' % (encode(k), encode(kwargs[k])) for k in sorted(kwargs)
                )
            ]))

        kwargs['oauth_signature'] = hmac(
            key, message, sha1
            ).digest().encode('base64')[:-1]

        return urlencode(kwargs)

    # who stole the cookie from the cookie jar?

    def get_cookie(self):
        return self.handler.request.cookies.get(
            'oauth.%s' % self.service, ''
            )

    def set_cookie(self, value, path='/'):
        self.handler.response.headers.add_header(
            'Set-Cookie', 
            '%s=%s; path=%s; expires="Fri, 31-Dec-2021 23:59:59 GMT"' %
            ('oauth.%s' % self.service, value, path)
            )

    def expire_cookie(self, path='/'):
        self.handler.response.headers.add_header(
            'Set-Cookie', 
            '%s=; path=%s; expires="Fri, 31-Dec-1999 23:59:59 GMT"' %
            ('oauth.%s' % self.service, path)
            )

    def get_xauth_token(self,username,password):
      _meth = 'POST' 
      api_method = 'https://api.twitter.com/oauth/access_token'
      service_info = self.service_info
      
      kwargs = {
        'oauth_consumer_key': service_info['consumer_key'],
        'oauth_nonce': getrandbits(64),
        'oauth_signature_method': 'HMAC-SHA1',
        'oauth_timestamp': int(time()),
        'oauth_version': '1.0'}
      params = {
        'x_auth_mode': 'client_auth',
        'x_auth_password': password,
        'x_auth_username': username
        }

      oauth_params = kwargs.copy()
      kwargs.update(params)

      message_array = [ _meth, api_method, '&'.join('%s=%s' % (encode(k), encode(kwargs[k])) for k in sorted(kwargs)) ]
      key = get_service_key(self.service)
      message = '&'.join(map(encode, message_array))

      oauth_params['oauth_signature'] = hmac(
          key, message, sha1
          ).digest().encode('base64')[:-1]

      oauth_header = "OAuth %s" % ', '.join("%s=\"%s\"" % (encode(k), encode(oauth_params[k])) for k in sorted(oauth_params))

      request_headers = {}
      request_headers['Authorization'] = oauth_header
      request_data = urlencode(params)

      fetch = urlfetch(url=api_method, headers=request_headers, payload=request_data, method='POST', deadline = 10)

      self.token = None
      if fetch.status_code == 200:
        key_name = create_uuid()

        self.token = OAuthAccessToken(
            key_name=key_name, service=self.service,
            **dict(t.split('=') for t in fetch.content.split('&'))
            )

        # TODO : Handle the GET timeout error in the following line
        if 'specifier_handler' in self.service_info:
          specifier = self.token.specifier = self.service_info['specifier_handler'](self)
          old = OAuthAccessToken.all().filter(
                'specifier =', specifier).filter(
                'service =', self.service)
          db.delete(old)

          self.token.put()

      return self.token


# ------------------------------------------------------------------------------
# oauth handler
# ------------------------------------------------------------------------------

class OAuthHandler(RequestHandler):

    def get(self, service, action=''):

        if service not in OAUTH_APP_SETTINGS:
            return self.response.out.write(
                "Unknown OAuth Service Provider: %r" % service
                )

        client = OAuthClient(service, self)

        try:
          if action in client.__public__:
            self.response.out.write(getattr(client, action)())
          else:
            self.response.out.write(client.login())
        except (DownloadError, ValueError, Timeout), e:
          logging.warning("Twitter:%s failed  :%s" % (action,e))
          flash = Flash()
          flash.msg = "Twitter is not responding currently. Please try again in some time. Hopefully it will be up."
          self.redirect("/")

# ------------------------------------------------------------------------------
# modify this demo MainHandler to suit your needs
# ------------------------------------------------------------------------------

HEADER = """
  <html><head><title>Twitter OAuth Demo</title>
  </head><body>
  <h1>Twitter OAuth Demo App</h1>
  """

FOOTER = "</body></html>"

class MainHandler(RequestHandler):
    """Demo Twitter App."""

    def get(self):

        client = OAuthClient('twitter', self)
        gdata = OAuthClient('google', self, scope='http://www.google.com/calendar/feeds')

        write = self.response.out.write; write(HEADER)

        if not client.get_cookie():
            write('<a href="/oauth/twitter/login">Login via Twitter</a>')
            write(FOOTER)
            return

        write('<a href="/oauth/twitter/logout">Logout from Twitter</a><br /><br />')

        info = client.get('/account/verify_credentials')

        write("<strong>Screen Name:</strong> %s<br />" % info['screen_name'])
        write("<strong>Location:</strong> %s<br />" % info['location'])

        rate_info = client.get('/account/rate_limit_status')

        write("<strong>API Rate Limit Status:</strong> %r" % rate_info)

        write(FOOTER)

# ------------------------------------------------------------------------------
# self runner -- gae cached main() function
# ------------------------------------------------------------------------------

def main():

    application = WSGIApplication([
       ('/oauth/(.*)/(.*)', OAuthHandler),
       ('/', MainHandler)
       ], debug=True)

    CGIHandler().run(application)

if __name__ == '__main__':
    main()
