"""使用者與kyc相關 serializer"""

from django.contrib.auth.models import User
from rest_framework import serializers
from member.models import UserProfileModel, KycRecordModel


class UserListSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(source="profile.phone_number", read_only=True)
    address = serializers.CharField(source="profile.address", read_only=True)

    class Meta:
        model = User
        exclude = ["password"]


class KYCListSerializer(serializers.ModelSerializer):
    id = serializers.ReadOnlyField(source='user_id')
    username = serializers.ReadOnlyField(source='user.username')
    latest_kyc_status = serializers.ReadOnlyField(source='get_latest_kyc_status_display')

    class Meta:
        model = UserProfileModel
        fields = ['id', 'username', 'latest_kyc_status', 'legal_name', 'birth_date', 'nationality']


class KYCRecordsSerializer(serializers.ModelSerializer):
    operator = serializers.SerializerMethodField()
    event_status = serializers.ReadOnlyField(source='get_event_status_display')

    class Meta:
        model = KycRecordModel
        fields = ['id', 'operator', 'event_status', 'legal_name', 'birth_date', 'nationality', 'reason', 'created_at']

    def get_operator(self, obj):
        if obj.operator:
            return obj.operator.last_name + obj.operator.first_name
        return ''


class KYCRetrieveSerializer(KYCListSerializer):
    kyc_records = serializers.SerializerMethodField()

    class Meta:
        model = UserProfileModel
        fields = ['id', 'username', 'latest_kyc_status', 'legal_name', 'birth_date', 'nationality', 'kyc_records']

    def get_kyc_records(self, obj):
        return KYCRecordsSerializer(KycRecordModel.objects.select_related('operator').filter(user=obj.user), many=True).data


class KYCCreateSerializer(serializers.ModelSerializer):
    legal_name = serializers.CharField()
    id_number = serializers.CharField()
    birth_date = serializers.DateField()
    nationality = serializers.CharField()

    class Meta:
        model = UserProfileModel
        fields = ['legal_name', 'id_number', 'birth_date', 'nationality']


class KYCApproveSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfileModel
        fields = ['kyc_tier']


class KYCReasonSerializer(serializers.ModelSerializer):
    reason = serializers.CharField()

    class Meta:
        model = KycRecordModel
        fields = ['reason']
