from django.contrib.auth.models import Group, Permission
from django.test import override_settings
from rest_framework.test import APITestCase

from members.models import User
from product.models import Location
from roles.models import Role, UserRoleAssignment
from roles.services import RoleService


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'roles-tests',
        }
    }
)
class RoleFlowTests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_user(
            email='super@example.com',
            password='pass123456',
            first_name='Super',
            last_name='User',
            is_staff=True,
            is_superuser=True,
        )

        self.staff = User.objects.create_user(
            email='staff@example.com',
            password='pass123456',
            first_name='Staff',
            last_name='User',
            is_staff=True,
            is_superuser=False,
        )

        self.user = User.objects.create_user(
            email='user@example.com',
            password='pass123456',
            first_name='Regular',
            last_name='User',
            is_staff=False,
            is_superuser=False,
        )

        self.parent_location = Location.objects.create(location='Warehouse A')
        self.child_location = Location.objects.create(location='Aisle 1', parent=self.parent_location)
        self.other_location = Location.objects.create(location='Warehouse B')

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def _create_role(self, payload, as_user=None):
        self._auth(as_user or self.superuser)
        return self.client.post('/roles/roles/', payload, format='json')

    def _assign(self, payload, as_user=None):
        self._auth(as_user or self.superuser)
        return self.client.post('/roles/assignments/', payload, format='json')

    def test_create_role_creates_group_and_no_duplicate_group(self):
        res = self._create_role({'name': 'Inventory Manager', 'description': 'Manages inventory'})
        self.assertEqual(res.status_code, 201)
        role_id = res.data['id']
        role = Role.objects.get(id=role_id)

        self.assertIsNotNone(role.required_group)
        self.assertEqual(role.required_group.name, 'Inventory Manager')
        self.assertEqual(Group.objects.filter(name='Inventory Manager').count(), 1)

        res2 = self._create_role({'name': 'Inventory Manager', 'description': 'Manages inventory'})
        self.assertEqual(res2.status_code, 400)
        self.assertEqual(Group.objects.filter(name='Inventory Manager').count(), 1)

    def test_location_based_role_validation(self):
        res = self._create_role({'name': 'LocRole', 'is_location_based': True})
        self.assertEqual(res.status_code, 400)

        res2 = self._create_role({'name': 'LocRole2', 'is_location_based': True, 'location_group_template': '{location_name} Manager'})
        self.assertEqual(res2.status_code, 201)

    def test_assign_role_to_user_adds_group_and_creates_assignment(self):
        role_res = self._create_role({'name': 'RoleA'})
        role = Role.objects.get(id=role_res.data['id'])

        res = self._assign({'user': self.user.id, 'role': role.id, 'reason': 'test'})
        self.assertEqual(res.status_code, 201)
        assignment = UserRoleAssignment.objects.get(id=res.data['id'])
        self.assertTrue(assignment.is_active)
        self.assertTrue(self.user.groups.filter(id=role.required_group.id).exists())

    def test_duplicate_assignment_errors(self):
        role_res = self._create_role({'name': 'RoleDup'})
        role = Role.objects.get(id=role_res.data['id'])

        res1 = self._assign({'user': self.user.id, 'role': role.id})
        self.assertEqual(res1.status_code, 201)

        res2 = self._assign({'user': self.user.id, 'role': role.id})
        self.assertEqual(res2.status_code, 400)

    def test_reactivation_reuses_same_record(self):
        role_res = self._create_role({'name': 'RoleReact'})
        role = Role.objects.get(id=role_res.data['id'])

        res1 = self._assign({'user': self.user.id, 'role': role.id})
        self.assertEqual(res1.status_code, 201)
        assignment_id = res1.data['id']

        self._auth(self.superuser)
        deact = self.client.post(f'/roles/assignments/{assignment_id}/deactivate/', format='json')
        self.assertEqual(deact.status_code, 200)
        self.assertFalse(UserRoleAssignment.objects.get(id=assignment_id).is_active)

        res2 = self._assign({'user': self.user.id, 'role': role.id, 'reason': 're-activate'})
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(res2.data['id'], assignment_id)
        self.assertEqual(UserRoleAssignment.objects.filter(user=self.user, role=role).count(), 1)

    def test_location_based_assignment_requires_location(self):
        role_res = self._create_role({'name': 'LocAssign', 'is_location_based': True, 'location_group_template': '{location_name} Manager'})
        role = Role.objects.get(id=role_res.data['id'])

        res = self._assign({'user': self.user.id, 'role': role.id})
        self.assertEqual(res.status_code, 400)

        res2 = self._assign({'user': self.user.id, 'role': role.id, 'location': str(self.child_location.id)})
        self.assertEqual(res2.status_code, 201)

        group_name = 'Warehouse A Manager'
        self.assertTrue(Group.objects.filter(name=group_name).exists())
        self.assertTrue(self.user.groups.filter(name=group_name).exists())

    def test_group_permission_inheritance_and_my_permissions_endpoint(self):
        perm = Permission.objects.filter(content_type__app_label='roles', codename='view_role').first()
        self.assertIsNotNone(perm)

        role_res = self._create_role({'name': 'PermRole'})
        role = Role.objects.get(id=role_res.data['id'])
        role.required_group.permissions.add(perm)

        self._assign({'user': self.staff.id, 'role': role.id})

        perms = RoleService.get_user_permissions_via_assignments(self.staff)
        self.assertIn(f'{perm.content_type.app_label}.{perm.codename}', perms)

        self._auth(self.staff)
        res = self.client.get('/roles/assignments/my-permissions/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(f'{perm.content_type.app_label}.{perm.codename}', set(res.data['permissions']))

        trace = RoleService.get_permission_trace_via_assignments(self.staff)
        for p in res.data['permissions']:
            self.assertIn(p, trace)
            self.assertGreaterEqual(len(trace[p]), 1)

    def test_removal_flow_removes_user_from_group(self):
        role_res = self._create_role({'name': 'RemoveRole'})
        role = Role.objects.get(id=role_res.data['id'])

        res1 = self._assign({'user': self.user.id, 'role': role.id})
        self.assertEqual(res1.status_code, 201)
        assignment_id = res1.data['id']
        self.assertTrue(self.user.groups.filter(id=role.required_group.id).exists())

        self._auth(self.superuser)
        deact = self.client.post(f'/roles/assignments/{assignment_id}/deactivate/', format='json')
        self.assertEqual(deact.status_code, 200)
        self.assertFalse(UserRoleAssignment.objects.get(id=assignment_id).is_active)
        self.assertFalse(self.user.groups.filter(id=role.required_group.id).exists())

    def test_multiple_location_assignments_remove_one_keeps_other(self):
        role_res = self._create_role({'name': 'LocMulti', 'is_location_based': True, 'location_group_template': '{location_name} Manager'})
        role = Role.objects.get(id=role_res.data['id'])

        a1 = self._assign({'user': self.user.id, 'role': role.id, 'location': str(self.parent_location.id)})
        self.assertEqual(a1.status_code, 201)
        a2 = self._assign({'user': self.user.id, 'role': role.id, 'location': str(self.other_location.id)})
        self.assertEqual(a2.status_code, 201)

        g1 = 'Warehouse A Manager'
        g2 = 'Warehouse B Manager'
        self.assertTrue(self.user.groups.filter(name=g1).exists())
        self.assertTrue(self.user.groups.filter(name=g2).exists())

        self._auth(self.superuser)
        deact = self.client.post(f'/roles/assignments/{a1.data["id"]}/deactivate/', format='json')
        self.assertEqual(deact.status_code, 200)
        self.assertFalse(self.user.groups.filter(name=g1).exists())
        self.assertTrue(self.user.groups.filter(name=g2).exists())

    def test_remove_location_based_requires_location_in_service(self):
        role_res = self._create_role({'name': 'LocRemove', 'is_location_based': True, 'location_group_template': '{location_name} Manager'})
        role = Role.objects.get(id=role_res.data['id'])
        with self.assertRaises(ValueError):
            RoleService.remove_role_from_user(self.user, role, location=None)

    def test_user_role_retrieval_unique_and_location_filtering(self):
        global_role = Role.objects.get(id=self._create_role({'name': 'GlobalRole'}).data['id'])
        loc_role = Role.objects.get(
            id=self._create_role({'name': 'LocRoleX', 'is_location_based': True, 'location_group_template': '{location_name} Manager'}).data['id']
        )

        self._assign({'user': self.user.id, 'role': global_role.id})
        self._assign({'user': self.user.id, 'role': loc_role.id, 'location': str(self.parent_location.id)})
        self._assign({'user': self.user.id, 'role': loc_role.id, 'location': str(self.other_location.id)})

        self._auth(self.superuser)
        res_all = self.client.get(f'/roles/users/{self.user.id}/roles/')
        self.assertEqual(res_all.status_code, 200)
        role_ids_all = {r['id'] for r in res_all.data}
        self.assertIn(global_role.id, role_ids_all)
        self.assertIn(loc_role.id, role_ids_all)

        res_loc = self.client.get(f'/roles/users/{self.user.id}/roles/?location_id={self.parent_location.id}')
        self.assertEqual(res_loc.status_code, 200)
        role_ids_loc = {r['id'] for r in res_loc.data}
        self.assertIn(global_role.id, role_ids_loc)
        self.assertIn(loc_role.id, role_ids_loc)

    def test_group_isolation_rule_and_sync(self):
        perm = Permission.objects.filter(content_type__app_label='roles', codename='view_role').first()
        role = Role.objects.get(id=self._create_role({'name': 'DriftRole'}).data['id'])
        role.required_group.permissions.add(perm)

        self.user.groups.add(role.required_group)
        self.assertTrue(self.user.get_all_permissions())

        self.assertEqual(RoleService.get_user_roles(self.user), [])
        self.assertEqual(RoleService.get_user_permissions_via_assignments(self.user), set())

        self._assign({'user': self.user.id, 'role': role.id})
        self.user.groups.remove(role.required_group)

        self._auth(self.superuser)
        sync = self.client.post(f'/roles/users/{self.user.id}/sync-groups/', format='json')
        self.assertEqual(sync.status_code, 200)
        self.assertTrue(self.user.groups.filter(id=role.required_group.id).exists())

        other_role = Role.objects.get(id=self._create_role({'name': 'OtherRole'}).data['id'])
        self.user.groups.add(other_role.required_group)
        sync2 = self.client.post(f'/roles/users/{self.user.id}/sync-groups/', format='json')
        self.assertEqual(sync2.status_code, 200)
        self.assertFalse(self.user.groups.filter(id=other_role.required_group.id).exists())

    def test_security_non_staff_cannot_assign_or_create_role(self):
        self._auth(self.user)
        res = self.client.post('/roles/roles/', {'name': 'DeniedRole'}, format='json')
        self.assertEqual(res.status_code, 403)

        role = Role.objects.get(id=self._create_role({'name': 'RoleForDenied'}, as_user=self.superuser).data['id'])
        self._auth(self.user)
        res2 = self.client.post('/roles/assignments/', {'user': self.user.id, 'role': role.id}, format='json')
        self.assertEqual(res2.status_code, 403)

    def test_dashboard_stats(self):
        r1 = Role.objects.get(id=self._create_role({'name': 'DashRole1'}).data['id'])
        r2 = Role.objects.get(id=self._create_role({'name': 'DashRole2'}).data['id'])
        self._assign({'user': self.user.id, 'role': r1.id})
        self._assign({'user': self.staff.id, 'role': r1.id})
        self._assign({'user': self.staff.id, 'role': r2.id})

        self._auth(self.superuser)
        res = self.client.get('/roles/dashboard/')
        self.assertEqual(res.status_code, 200)
        stats = res.data['statistics']
        self.assertEqual(stats['total_roles'], Role.objects.filter(is_active=True).count())
        self.assertEqual(stats['total_assignments'], UserRoleAssignment.objects.filter(is_active=True).count())

    def test_invalid_role_user_location_assignment(self):
        role = Role.objects.get(id=self._create_role({'name': 'ValidRole'}).data['id'])

        res1 = self._assign({'user': 999999, 'role': role.id})
        self.assertEqual(res1.status_code, 400)

        res2 = self._assign({'user': self.user.id, 'role': 999999})
        self.assertEqual(res2.status_code, 400)

        res3 = self._assign({'user': self.user.id, 'role': role.id, 'location': '00000000-0000-0000-0000-000000000000'})
        self.assertEqual(res3.status_code, 400)

    def test_cache_invalidation_on_assign_and_remove(self):
        role = Role.objects.get(id=self._create_role({'name': 'CacheRole'}).data['id'])

        self.assertEqual(RoleService.get_user_roles(self.user), [])
        self._assign({'user': self.user.id, 'role': role.id})
        self.assertEqual({r.id for r in RoleService.get_user_roles(self.user)}, {role.id})

        assignment = UserRoleAssignment.objects.get(user=self.user, role=role)
        RoleService.remove_role_from_user(self.user, role, location=assignment.location)
        self.assertEqual(RoleService.get_user_roles(self.user), [])

    def test_concurrent_duplicate_assignments_one_fails(self):
        role = Role.objects.get(id=self._create_role({'name': 'ConcurrencyRole'}).data['id'])

        res1 = self._assign({'user': self.user.id, 'role': role.id})
        self.assertEqual(res1.status_code, 201)

        res2 = self._assign({'user': self.user.id, 'role': role.id})
        self.assertEqual(res2.status_code, 400)
