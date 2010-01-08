import cgi
import datetime
import wsgiref.handlers
import logging
import base64
import os
import urllib2
import re

from google.appengine.ext import db
from google.appengine.api import users
from google.appengine.ext import webapp
from google.appengine.api import urlfetch
from google.appengine.ext.webapp import template
from google.appengine.api.datastore_errors import Timeout

from twitter_oauth_handler import OAuthClient
from twitter_oauth_handler import OAuthHandler
from twitter_oauth_handler import OAuthAccessToken

from demjson import decode as decode_json

class TwitterUser(db.Model):
  user = db.StringProperty()
  basic_auth = db.StringProperty()
  phonenumber = db.StringProperty()
  active = db.IntegerProperty(default = 0)
  tweetCount = db.IntegerProperty(default = 0)
  accessTokenid = db.StringProperty()

  @staticmethod
  def __key_name(phnum):
    return "key_%s" % phnum.replace("+","")

  @classmethod
  def get_by_phonenumber(cls, phnum):
    count = 0
    while count < 3:
      try:
        return TwitterUser.get_by_key_name(TwitterUser.__key_name(phnum))
      except Timeout, e:
        logging.error("Timedout. Will try again")
        count += 1
    return None # Timed out after 3 attempts

  @classmethod
  def create_by_phonenumber(cls, phnum, user, passwd = None):
    basic_auth = None
    if passwd:
      basic_auth = base64.encodestring('%s:%s' % (user, passwd))[:-1]
    tu = TwitterUser(key_name=TwitterUser.__key_name(phnum), user = user, basic_auth = basic_auth , phonenumber = phnum , active = 1)
    k = tu.put()

    # New user has joined in. Follow him and post a welcome message
    sms_client = OAuthClient('twitter', cls)
    sms_client.token = OAuthAccessToken.all().filter(
                'specifier =', 'smstweetin').filter(
                'service =', 'twitter').fetch(1)[0]

    try:
      info = sms_client.post('/friendships/create', 'POST', (200,401,403), screen_name=user)  # TODO : this may fail, try three times 
      # Stop sending the follow status
      #status = "@%s has started using SMSTweet. Welcome %s to the group and tell about us to your friends" % (user, user)
      #info = sms_client.post('/statuses/update', 'POST', (200,401), status=status)  # TODO : this may fail, try three times 
    except (urlfetch.DownloadError, ValueError), e:
      logging.error("SmsTweetin:Friendship/create failed %s" % e)

    return tu


