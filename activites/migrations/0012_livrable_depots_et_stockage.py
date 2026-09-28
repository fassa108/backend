import activites.models
import django.core.validators
from django.db import migrations, models


def numeroter_depots(apps, schema_editor):
    """Numérote les dépôts existants de chaque assignation, par date."""
    Livrable = apps.get_model("activites", "Livrable")
    compteurs = {}
    for livrable in Livrable.objects.order_by("assignation_id", "date_depot", "id"):
        compteurs[livrable.assignation_id] = compteurs.get(livrable.assignation_id, 0) + 1
        livrable.numero = compteurs[livrable.assignation_id]
        livrable.save(update_fields=["numero"])


class Migration(migrations.Migration):

    dependencies = [
        ("activites", "0011_categories_briefs"),
    ]

    operations = [
        # Dépôt : numéro, commentaire ; plus de titre ni de statut
        migrations.AddField(
            model_name="livrable",
            name="numero",
            field=models.PositiveIntegerField(default=1),
            preserve_default=False,
        ),
        migrations.RunPython(numeroter_depots, migrations.RunPython.noop),
        migrations.RenameField(
            model_name="livrable",
            old_name="description",
            new_name="commentaire",
        ),
        migrations.RemoveField(model_name="livrable", name="titre"),
        migrations.RemoveField(model_name="livrable", name="statut"),
        migrations.RemoveField(model_name="livrable", name="date_modification"),
        migrations.AlterModelOptions(
            name="livrable",
            options={"ordering": ["-date_depot"]},
        ),
        migrations.AddConstraint(
            model_name="livrable",
            constraint=models.UniqueConstraint(
                fields=("assignation", "numero"),
                name="unique_numero_depot_par_assignation",
            ),
        ),
        # Stockage : noms aléatoires, rangés par organisme (et par brief)
        migrations.AlterField(
            model_name="ressource",
            name="fichier",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=activites.models.chemin_ressource,
                validators=[django.core.validators.FileExtensionValidator(allowed_extensions=["pdf", "pptx", "docx", "txt"])],
            ),
        ),
        migrations.AlterField(
            model_name="fichierlivrable",
            name="fichier",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=activites.models.chemin_fichier_livrable,
                validators=[django.core.validators.FileExtensionValidator(allowed_extensions=["pdf", "pptx", "docx", "txt"])],
            ),
        ),
    ]
