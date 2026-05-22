from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Location, Stock


class Command(BaseCommand):
    help = "Seed 5 locations and 5 parts under each location"

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefix",
            default="Location",
            help="Location name prefix (default: Location)",
        )
        parser.add_argument(
            "--parts-prefix",
            default="Part",
            help="Part name prefix (default: Part)",
        )
        parser.add_argument(
            "--locations",
            type=int,
            default=5,
            help="Number of locations to create (default: 5)",
        )
        parser.add_argument(
            "--parts-per-location",
            type=int,
            default=5,
            help="Number of parts to create under each location (default: 5)",
        )
        parser.add_argument(
            "--starting-balance",
            type=int,
            default=10,
            help="Starting stock balance for each part (default: 10)",
        )
        parser.add_argument(
            "--starting-price",
            type=int,
            default=100,
            help="Starting price for each part (default: 100)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        loc_prefix = str(options["prefix"]).strip() or "Location"
        part_prefix = str(options["parts_prefix"]).strip() or "Part"
        loc_count = max(0, int(options["locations"]))
        per_loc = max(0, int(options["parts_per_location"]))
        starting_balance = int(options["starting_balance"])
        starting_price = int(options["starting_price"])

        created_locations = 0
        created_parts = 0
        updated_parts = 0

        locations = []
        for i in range(1, loc_count + 1):
            name = f"{loc_prefix} {i}"
            loc, created = Location.objects.get_or_create(location=name, parent=None)
            if created:
                created_locations += 1
            locations.append(loc)

        for loc_idx, loc in enumerate(locations, start=1):
            for part_idx in range(1, per_loc + 1):
                part_name = f"{part_prefix} {part_idx}"
                part_number = f"L{loc_idx:02d}-P{part_idx:03d}"
                stock, created = Stock.objects.get_or_create(
                    location=loc,
                    part_number=part_number,
                    defaults={
                        "part_name": part_name,
                        "balance": starting_balance,
                        "price": starting_price,
                    },
                )
                if created:
                    created_parts += 1
                else:
                    changed = False
                    if stock.part_name != part_name:
                        stock.part_name = part_name
                        changed = True
                    if stock.balance != starting_balance:
                        stock.balance = starting_balance
                        changed = True
                    if stock.price != starting_price:
                        stock.price = starting_price
                        changed = True
                    if changed:
                        stock.save(update_fields=["part_name", "balance", "price"])
                        updated_parts += 1

        self.stdout.write(self.style.SUCCESS(f"Locations: created {created_locations} (target {loc_count})"))
        self.stdout.write(self.style.SUCCESS(f"Parts: created {created_parts}, updated {updated_parts} (per location {per_loc})"))

