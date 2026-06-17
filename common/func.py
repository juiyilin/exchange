from django.contrib.auth.models import User
import random
import uuid


def get_random_user():
    user_ids = User.objects.values_list("id", flat=True)
    # return random.choice(user_ids)
    return User.objects.first()


def generate_hex_uuid():
    return uuid.uuid4().hex