class OrderNotCancelable(Exception):
    def __init__(self, order):
        self.order = order
        super().__init__(f"本單{order.get_status_display()}，無法取消")