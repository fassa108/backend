from rest_framework import serializers

from .models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "role", "contenu", "statut", "date_creation"]
        read_only_fields = fields


class ConversationSerializer(serializers.ModelSerializer):
    brief_titre = serializers.CharField(source="brief.titre", read_only=True, default=None)
    brief_date_limite = serializers.DateTimeField(source="brief.date_limite", read_only=True, default=None)

    class Meta:
        model = Conversation
        fields = ["id", "titre", "brief", "brief_titre", "brief_date_limite", "date_creation", "date_modification"]
        read_only_fields = ["id", "titre", "brief_titre", "brief_date_limite", "date_creation", "date_modification"]


class ConversationDetailSerializer(ConversationSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta(ConversationSerializer.Meta):
        fields = [*ConversationSerializer.Meta.fields, "messages"]
