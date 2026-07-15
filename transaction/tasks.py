from celery import shared_task
from transaction.services import match_order


@shared_task
def send_to_match_market(order_id, trading_pair_id):
    match_order(order_id, trading_pair_id)
