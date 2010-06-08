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

from time import sleep

class TwitterProfile(db.Model):
  id = db.StringProperty()
  name = db.StringProperty()
  screen_name = db.StringProperty()
  location = db.StringProperty()
  description = db.TextProperty()
  profile_image_url = db.StringProperty()
  url = db.StringProperty()
  followers_count = db.IntegerProperty()
  friends_count = db.IntegerProperty()
  statuses_count = db.IntegerProperty()

  access_token = db.ReferenceProperty(OAuthAccessToken)
  last_updated = db.DateTimeProperty(auto_now = True)

  @staticmethod
  def __key_name(_id):
    return "id_%s" % _id

  @classmethod
  def get_by_id(cls, _id):
    return TwitterProfile.get_by_key_name(TwitterProfile.__key_name(_id))

  @classmethod
  def save_twitter_profile(cls,info,token):
    tp = None
    if 'id' in info:
      _id = "%s" % info['id']
      tp = TwitterProfile.get_by_id(_id)
      if tp == None:
        tp = TwitterProfile(key_name = TwitterProfile.__key_name(_id))
        tp.id = _id
      tp.name = info['name']
      tp.screen_name = info['screen_name']
      tp.location = info['location']
      tp.description = info['description']
      tp.profile_image_url = info['profile_image_url']
      tp.url = info['url']
      tp.followers_count = info['followers_count']
      tp.friends_count = info['friends_count']
      tp.statuses_count = info['statuses_count']
      tp.access_token = token
      tp.put()

    return tp

  @classmethod
  def get_twitter_profile(cls,tuser):
    client = OAuthClient('twitter', cls)
    info = client.get('/account/verify_credentials', 'GET', (200,401), tuser)
    tp = None
    if 'id' in info:
      tp = TwitterProfile.save_twitter_profile(info,client.token)
    elif 'error' in info:
      print "profile %s gave error %s. Deleting the record" % (tuser.user, info['error'])
      tuser.delete()

    return tp

  @classmethod
  def report_stale_numbers(cls, offset = 0):
    tusers = TwitterUser.all().filter("active =", 1).fetch(1000, offset)
    for t in tusers:
      print "Trying to get profile for %s ..." % t.user
      tp = TwitterProfile.get_twitter_profile(t)
      if tp == None:
        print "Could not get twitter profile for above user"
      else:
        p = Phonenumber.get_phonenumber(t,tp)
        t.active = 2
        t.put()
      sleep(5)

class Phonenumber(db.Model):
  phonenumber = db.StringProperty()
  tprofile = db.ReferenceProperty(TwitterProfile)
  location = db.StringProperty(default = "")
  carrier = db.StringProperty(default = "")
  reminder = db.IntegerProperty(default = 0)
  lastError = db.StringProperty()

  @staticmethod
  def __key_name(number):
    return "ph_%s" % number

  @classmethod
  def get_by_number(cls, number):
    return Phonenumber.get_by_key_name(Phonenumber.__key_name(number))

  @classmethod
  def get_phonenumber(cls,tuser,tprofile):
    tp = None
    if tuser.phonenumber:
      tp = Phonenumber.get_by_number(tuser.phonenumber)
      if tp == None:
        tp = Phonenumber(key_name = Phonenumber.__key_name(tuser.phonenumber))
        tp.phonenumber = tuser.phonenumber
        tp.tprofile = tprofile
        tp.location = tuser.location
        tp.carrier = tuser.carrier
        tp.reminder = tuser.reminder
        tp.lastError = tuser.lastError

        tp.put()

    return tp


class TwitterUser(db.Model):
  user = db.StringProperty()
  basic_auth = db.StringProperty()
  phonenumber = db.StringProperty()
  active = db.IntegerProperty(default = 0)
  tweetCount = db.IntegerProperty(default = 0)
  accessTokenid = db.StringProperty()
  location = db.StringProperty(default = "")
  carrier = db.StringProperty(default = "")
  reminder = db.IntegerProperty(default = 0)
  lastError = db.StringProperty()

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

  def get_last_error(self):
    ret = None
    if self.lastError:
      ret = self.lastError
      self.lastError = None
      self.put()
    return ret

  def incr_counter(self, location = None, carrier = None):
    self.tweetCount += 1
    if location: self.location = location
    if carrier:  self.carrier = carrier
    self.put()

  def fetch_mentions_and_dms(self):
    taskqueue.add(url = '/tasks/fetch/mentions', params = { 'phone' : self.phonenumber })
    taskqueue.add(url = '/tasks/fetch/dms', params = { 'phone' : self.phonenumber })
