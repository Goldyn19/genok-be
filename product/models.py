from django.db import models
import uuid
from django.core.exceptions import ValidationError


class Location(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.CharField(max_length=50)
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children'
    )

    def __str__(self):
        return self.location

    def full_path(self):
        path = [self.location]
        parent = self.parent
        while parent:
            path.append(parent.location)
            parent = parent.parent
            return '/'.join(reversed(path))


class Stock(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    part_name = models.CharField(max_length=80)
    part_number = models.CharField(max_length=50, db_index=True)
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    balance = models.IntegerField()
    price = models.IntegerField(null=True, blank=True)
    is_caterpillar = models.BooleanField(default=True)
    brand = models.CharField(max_length=80, blank=True, null=True)
    is_original = models.BooleanField(default=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="variants"
    )

    def clean(self):
        if self.parent == self:
            raise ValidationError("Part cannot be its own parent")

    def __str__(self):
        return f'{self.part_name} {self.part_number}'


# Create your models here.
