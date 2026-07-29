from django.db import models


class KycStatus(models.TextChoices):
    UNVERIFIED = 'UNVERIFIED', '未驗證'
    VERIFYING = 'VERIFYING', '審核中'
    APPROVED = 'APPROVED', '已通過'
    REJECTED = 'REJECTED', '已拒絕'


class KycEvent(models.TextChoices):
    SUBMITTED = "SUBMITTED", "送審"
    APPROVED = "APPROVED", "通過"
    REJECTED = "REJECTED", "拒絕"
    REVOKED = "REVOKED", "撤銷"       # 風控/懲罰性作廢(詐欺/盜用/制裁)
    REVERIFY_REQUIRED = "REVERIFY_REQUIRED", "要求重驗"   # 例行重新驗證


class Role(models.TextChoices):
    TRADER = "trader", "交易者"
    SUPPORT = "support", "客服"
    COMPLIANCE = "compliance", "合規"
    ADMIN = "admin", "管理員"


class KycTierLevel(models.IntegerChoices):
    HIGH = 0, '高風險'
    MEDIUM = 1, '一般風險'
    LOW = 2, '低風險'


KYC_TIER_DAILY_LIMIT = {  # TODO:之後需改成可讓admin設定
    KycTierLevel.HIGH: 0,
    KycTierLevel.MEDIUM: 100000,
    KycTierLevel.LOW: None
}