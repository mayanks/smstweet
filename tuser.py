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
from google.appengine.api.labs import taskqueue

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
  location = db.StringProperty(default = "")
  carrier = db.StringProperty(default = "")

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
        logging.warning("Timedout. Will try again")
        count += 1
    logging.error("Timedout (get_by_phonenumber). after 3 attempts")
    return None # Timed out after 3 attempts

  @classmethod
  def create_by_phonenumber(cls, phnum, user, passwd = None):
    basic_auth = None
    if passwd:
      basic_auth = base64.encodestring('%s:%s' % (user, passwd))[:-1]
    tu = TwitterUser(key_name=TwitterUser.__key_name(phnum), user = user, basic_auth = basic_auth , phonenumber = phnum , active = 1)
    k = tu.put()

    # New user has joined in. Follow him and post a welcome message
    taskqueue.add(url = '/tasks/follow_new_user', params = { 'screen_name' : user, 'count' : 1 })

    return tu

  def incr_counter(self, location = None, carrier = None):
    self.tweetCount += 1
    if location: self.location = location
    if carrier:  self.carrier = carrier
    self.put()

  def fetch_mentions_and_dms(self):
    taskqueue.add(url = '/tasks/fetch/mentions', params = { 'phone' : self.phonenumber })
    taskqueue.add(url = '/tasks/fetch/dms', params = { 'phone' : self.phonenumber })
