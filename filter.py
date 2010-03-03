import datetime
from google.appengine.ext.webapp import template

def human_date(value):
  delta = datetime.datetime.now() - value
  if delta.days > 6:
    return 'on ' + value.strftime("%b %d")                    # May 15
  if delta.days > 1:
    return 'on ' + value.strftime("%A")                       # Wednesday
  elif delta.days == 1:
    return 'yesterday'                                # yesterday
  elif delta.seconds > 3600:
    return 'about ' + str(delta.seconds / 3600 ) + ' hours ago'  # 3 hours ago
  elif delta.seconds >  120:
    return 'about ' + str(delta.seconds/60) + ' minutes ago'     # 29 minutes ago
  else:
    return 'a moment ago'                             # a moment ago

register = template.create_template_register()
register.filter(human_date)

