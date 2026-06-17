from django.contrib.auth.models import User
import random
import uuid


def get_random_user_id():
    user_ids = User.objects.values_list("id", flat=True)
    # return random.choice(user_ids)
    return 1


def generate_hex_uuid():
    return uuid.uuid4().hex