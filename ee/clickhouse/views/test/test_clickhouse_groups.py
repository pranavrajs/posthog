from uuid import UUID, uuid4

from freezegun.api import freeze_time

from ee.clickhouse.models.event import create_event
from ee.clickhouse.models.group import create_group
from ee.clickhouse.util import ClickhouseTestMixin, snapshot_clickhouse_queries
from posthog.models import Event, GroupTypeMapping, Person
from posthog.test.base import APIBaseTest


def _create_event(**kwargs):
    kwargs["event_uuid"] = uuid4()
    create_event(**kwargs)


class ClickhouseTestGroupsApi(ClickhouseTestMixin, APIBaseTest):
    maxDiff = None

    @freeze_time("2021-05-02")
    def test_groups_list(self):
        with freeze_time("2021-05-01"):
            create_group(
                team_id=self.team.pk,
                group_type_index=0,
                group_key="org:5",
                properties={"industry": "finance", "name": "Mr. Krabs"},
            )
        with freeze_time("2021-05-02"):
            create_group(
                team_id=self.team.pk, group_type_index=0, group_key="org:6", properties={"industry": "technology"},
            )
        create_group(
            team_id=self.team.pk, group_type_index=1, group_key="company:1", properties={"name": "Plankton"},
        )

        response = self.client.get(f"/api/projects/{self.team.id}/groups?group_type_index=0").json()
        self.assertEqual(
            response,
            {
                "next": None,
                "previous": None,
                "results": [
                    {
                        "created_at": "2021-05-02T00:00:00Z",
                        "group_key": "org:6",
                        "group_properties": {"industry": "technology"},
                        "group_type_index": 0,
                    },
                    {
                        "created_at": "2021-05-01T00:00:00Z",
                        "group_key": "org:5",
                        "group_properties": {"industry": "finance", "name": "Mr. Krabs"},
                        "group_type_index": 0,
                    },
                ],
            },
        )

    @freeze_time("2021-05-02")
    def test_retrieve_group(self):
        create_group(
            team_id=self.team.pk,
            group_type_index=0,
            group_key="key",
            properties={"industry": "finance", "name": "Mr. Krabs"},
        )
        create_group(
            team_id=self.team.pk, group_type_index=1, group_key="foo//bar", properties={},
        )

        fail_response = self.client.get(f"/api/projects/{self.team.id}/groups/find?group_type_index=1&group_key=key")
        self.assertEqual(fail_response.status_code, 404)

        ok_response = self.client.get(f"/api/projects/{self.team.id}/groups/find?group_type_index=0&group_key=key")
        self.assertEqual(ok_response.status_code, 200)
        self.assertEqual(
            ok_response.json(),
            {
                "created_at": "2021-05-02T00:00:00Z",
                "group_key": "key",
                "group_properties": {"industry": "finance", "name": "Mr. Krabs"},
                "group_type_index": 0,
            },
        )
        ok_response = self.client.get(f"/api/projects/{self.team.id}/groups/find?group_type_index=1&group_key=foo//bar")
        self.assertEqual(ok_response.status_code, 200)
        self.assertEqual(
            ok_response.json(),
            {
                "created_at": "2021-05-02T00:00:00Z",
                "group_key": "foo//bar",
                "group_properties": {},
                "group_type_index": 1,
            },
        )

    @freeze_time("2021-05-10")
    @snapshot_clickhouse_queries
    def test_related_groups(self):
        uuid = self._create_related_groups_data()

        response = self.client.get(f"/api/projects/{self.team.id}/groups/related?id=0::0&group_type_index=0").json()
        self.assertEqual(
            response,
            [
                {
                    "type": "person",
                    "id": str(uuid),
                    "group_type_index": None,
                    "person": {"distinct_ids": ["1"], "properties": {}},
                },
                {"type": "group", "id": "1::2", "group_type_index": 1, "person": None},
                {"type": "group", "id": "1::3", "group_type_index": 1, "person": None},
            ],
        )

    @freeze_time("2021-05-10")
    @snapshot_clickhouse_queries
    def test_related_groups_person(self):
        uuid = self._create_related_groups_data()

        response = self.client.get(f"/api/projects/{self.team.id}/groups/related?id={uuid}").json()
        self.assertEqual(
            response,
            [
                {"type": "group", "id": "0::0", "group_type_index": 0, "person": None},
                {"type": "group", "id": "0::1", "group_type_index": 0, "person": None},
                {"type": "group", "id": "1::2", "group_type_index": 1, "person": None},
                {"type": "group", "id": "1::3", "group_type_index": 1, "person": None},
            ],
        )

    def test_property_definitions(self):
        create_group(
            team_id=self.team.pk,
            group_type_index=0,
            group_key="org:5",
            properties={"industry": "finance", "name": "Mr. Krabs"},
        )
        create_group(team_id=self.team.pk, group_type_index=0, group_key="org:6", properties={"industry": "technology"})
        create_group(team_id=self.team.pk, group_type_index=1, group_key="company:1", properties={"name": "Plankton"})
        create_group(team_id=self.team.pk, group_type_index=1, group_key="company:2", properties={})

        response = self.client.get(f"/api/projects/{self.team.id}/groups/property_definitions").json()
        self.assertEqual(
            response,
            {
                "0": [{"name": "industry", "count": 2}, {"name": "name", "count": 1}],
                "1": [{"name": "name", "count": 1}],
            },
        )

    def test_property_values(self):
        create_group(team_id=self.team.pk, group_type_index=0, group_key="org:5", properties={"industry": "finance"})
        create_group(team_id=self.team.pk, group_type_index=0, group_key="org:6", properties={"industry": "technology"})
        create_group(team_id=self.team.pk, group_type_index=1, group_key="org:1", properties={"industry": "finance"})
        response = self.client.get(
            f"/api/projects/{self.team.id}/groups/property_values/?key=industry&group_type_index=0"
        ).json()
        self.assertEqual(len(response), 2)
        self.assertEqual(response, [{"name": "finance"}, {"name": "technology"}])

    def test_empty_property_values(self):
        create_group(team_id=self.team.pk, group_type_index=0, group_key="org:5", properties={"industry": "finance"})
        create_group(team_id=self.team.pk, group_type_index=0, group_key="org:6", properties={"industry": "technology"})
        create_group(team_id=self.team.pk, group_type_index=1, group_key="org:1", properties={"industry": "finance"})
        response = self.client.get(
            f"/api/projects/{self.team.id}/groups/property_values/?key=name&group_type_index=0"
        ).json()
        self.assertEqual(len(response), 0)
        self.assertEqual(response, [])

    def _create_related_groups_data(self):
        GroupTypeMapping.objects.create(team=self.team, group_type="organization", group_type_index=0)
        GroupTypeMapping.objects.create(team=self.team, group_type="playlist", group_type_index=1)

        uuid = UUID("01795392-cc00-0003-7dc7-67a694604d72")

        p1 = Person.objects.create(uuid=uuid, team_id=self.team.pk, distinct_ids=["1", "2"])
        p2 = Person.objects.create(team_id=self.team.pk, distinct_ids=["3"])
        p3 = Person.objects.create(team_id=self.team.pk, distinct_ids=["4"])

        create_group(self.team.pk, 0, "0::0")
        create_group(self.team.pk, 0, "0::1")
        create_group(self.team.pk, 1, "1::2")
        create_group(self.team.pk, 1, "1::3")
        create_group(self.team.pk, 1, "1::4")
        create_group(self.team.pk, 1, "1::5")

        _create_event(
            event="$pageview",
            team=self.team,
            distinct_id="1",
            timestamp="2021-05-05 00:00:00",
            properties={"$group_0": "0::0", "$group_1": "1::2"},
        )

        _create_event(
            event="$pageview",
            team=self.team,
            distinct_id="1",
            timestamp="2021-05-05 00:00:00",
            properties={"$group_0": "0::0", "$group_1": "1::3"},
        )

        _create_event(
            event="$pageview",
            team=self.team,
            distinct_id="1",
            timestamp="2021-05-05 00:00:00",
            properties={"$group_0": "0::1", "$group_1": "1::3"},
        )

        # Event too old, not counted
        _create_event(
            event="$pageview",
            team=self.team,
            distinct_id="1",
            timestamp="2000-05-05 00:00:00",
            properties={"$group_0": "0::0", "$group_1": "1::4"},
        )

        # No such group exists in groups table
        _create_event(
            event="$pageview",
            team=self.team,
            distinct_id="1",
            timestamp="2000-05-05 00:00:00",
            properties={"$group_0": "0::0", "$group_1": "no such group"},
        )

        return uuid
