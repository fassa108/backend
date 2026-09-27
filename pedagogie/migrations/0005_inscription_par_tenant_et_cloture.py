import django.db.models.deletion
from django.db import migrations, models


def remplir_tenant(apps, schema_editor):
    """Recopie l'organisme de la promotion sur les inscriptions existantes."""
    InscriptionPromotion = apps.get_model("pedagogie", "InscriptionPromotion")
    for inscription in InscriptionPromotion.objects.select_related("promotion__formation"):
        inscription.tenant_id = inscription.promotion.formation.tenant_id
        inscription.save(update_fields=["tenant"])


class Migration(migrations.Migration):

    dependencies = [
        ("pedagogie", "0004_add_formateur_promotion"),
        ("tenants", "0002_alter_tenant_nom"),
    ]

    operations = [
        # Inscription : organisme (d'abord nullable, rempli, puis obligatoire)
        migrations.AddField(
            model_name="inscriptionpromotion",
            name="tenant",
            field=models.ForeignKey(
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inscriptions",
                to="tenants.tenant",
            ),
        ),
        migrations.RunPython(remplir_tenant, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="inscriptionpromotion",
            name="tenant",
            field=models.ForeignKey(
                editable=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inscriptions",
                to="tenants.tenant",
            ),
        ),
        # Une inscription active par organisme (et non plus sur la plateforme)
        migrations.RemoveConstraint(
            model_name="inscriptionpromotion",
            name="unique_promotion_active_par_apprenant",
        ),
        migrations.AddConstraint(
            model_name="inscriptionpromotion",
            constraint=models.UniqueConstraint(
                condition=models.Q(("actif", True)),
                fields=("apprenant", "tenant"),
                name="unique_promotion_active_par_apprenant_et_tenant",
            ),
        ),
        # Clôture / réouverture de promotion
        migrations.AddField(
            model_name="inscriptionpromotion",
            name="fermee_par_cloture",
            field=models.BooleanField(default=False),
        ),
        # Appartenance à un groupe conservée mais inactive après désinscription
        migrations.AddField(
            model_name="groupemembre",
            name="actif",
            field=models.BooleanField(default=True),
        ),
    ]
