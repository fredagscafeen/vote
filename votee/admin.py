from django.contrib import admin
from votee import models

admin.site.register(models.Election)
admin.site.register(models.Poll)
admin.site.register(models.PollOption)
admin.site.register(models.UsedBallot)
