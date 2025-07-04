# This file is part of Checkbox.
#
# Copyright 2012, 2013 Canonical Ltd.
# Written by:
#   Zygmunt Krynicki <zygmunt.krynicki@canonical.com>
#
# Checkbox is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3,
# as published by the Free Software Foundation.
#
# Checkbox is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Checkbox.  If not, see <http://www.gnu.org/licenses/>.

"""
plainbox.impl.test_ctrl
=======================

Test definitions for plainbox.impl.ctrl module
"""

from unittest import TestCase
import json

from plainbox.abc import IJobResult
from plainbox.suspend_consts import Suspend
from plainbox.impl.ctrl import (
    CheckBoxSessionStateController,
    SymLinkNest,
    gen_rfc822_records_from_io_log,
)

from plainbox.impl.depmgr import DependencyType
from plainbox.impl.job import JobDefinition
from plainbox.impl.unit.job import InvalidJob
from plainbox.impl.resource import Resource, ResourceExpression
from plainbox.impl.secure.origin import JobOutputTextSource, Origin
from plainbox.impl.secure.providers.v1 import Provider1
from plainbox.impl.secure.rfc822 import RFC822Record, RFC822SyntaxError
from plainbox.impl.session import (
    InhibitionCause,
    JobReadinessInhibitor,
    JobState,
    SessionState,
)

from plainbox.impl.testing_utils import make_job
from plainbox.impl.unit.template import TemplateUnit
from plainbox.impl.validation import Severity
from plainbox.impl.unit.unit import MissingParam
from plainbox.vendor import mock


class CheckBoxSessionStateControllerTests(TestCase):

    def setUp(self):
        self.ctrl = CheckBoxSessionStateController()

    def test_get_dependency_set(self):
        # Job with no dependencies
        job_a = JobDefinition({})
        self.assertEqual(self.ctrl.get_dependency_set(job_a), set())
        # Job with "depends" dependencies
        job_b = JobDefinition({"depends": "j1, j2"})
        self.assertEqual(
            self.ctrl.get_dependency_set(job_b),
            {(DependencyType.DEPENDS, "j1"), (DependencyType.DEPENDS, "j2")},
        )
        # Job with resouce dependencies
        job_c = JobDefinition({"requires": "j3.attr == 1"})
        self.assertEqual(
            self.ctrl.get_dependency_set(job_c),
            {(DependencyType.RESOURCE, "j3")},
        )
        # Job with after dependencies
        job_d = JobDefinition({"after": "j1, j2"})
        self.assertEqual(
            self.ctrl.get_dependency_set(job_d),
            {(DependencyType.AFTER, "j1"), (DependencyType.AFTER, "j2")},
        )
        # Job with both depends and resource dependencies
        job_e = JobDefinition({"depends": "j4", "requires": "j5.attr == 1"})
        self.assertEqual(
            self.ctrl.get_dependency_set(job_e),
            {(DependencyType.DEPENDS, "j4"), (DependencyType.RESOURCE, "j5")},
        )
        # Job with both depends and resource dependencies
        # on the same job (j6)
        job_f = JobDefinition({"depends": "j6", "requires": "j6.attr == 1"})
        self.assertEqual(
            self.ctrl.get_dependency_set(job_f),
            {(DependencyType.DEPENDS, "j6"), (DependencyType.RESOURCE, "j6")},
        )
        # Job with an "also-after-suspend" flag, meaning this job should be
        # set to run before the suspend job
        job_g = JobDefinition({"id": "j7", "flags": Suspend.AUTO_FLAG})
        suspend_job = JobDefinition({"id": Suspend.AUTO_JOB_ID})
        self.assertEqual(
            self.ctrl.get_dependency_set(suspend_job, [job_g]),
            {(DependencyType.AFTER, "j7")},
        )

    def test_add_before_deps(self):
        """
        Verify that add_before_deps() adds all "before" references declared
        in a job to the corresponding jobs as an "after" dependency.
        """
        job_a = JobDefinition({"id": "a", "before": "c"})
        job_b = JobDefinition({"id": "b", "before": "c"})
        job_c = JobDefinition({"id": "c"})
        job_map = {"a": job_a, "b": job_b, "c": job_c}
        global_job_map = job_map
        # Add before dependencies
        self.ctrl.add_before_deps(job_a, job_map, global_job_map)
        self.ctrl.add_before_deps(job_b, job_map, global_job_map)
        self.assertIn("a", job_c.before_references)
        self.assertIn("b", job_c.before_references)
        self.assertEqual(len(job_c.before_references), 2)

    @mock.patch("plainbox.impl.ctrl.logger")
    def test_add_before_deps_missing_dep(self, mock_logger):
        """
        Verify that add_before_deps() raises DependencyMissingError
        if a job has a "before" dependency on a job that does not exist.
        """
        job_a = JobDefinition({"id": "a", "before": "b c"})
        job_map = {"a": job_a}
        global_job_map = {
            "a": JobDefinition({"id": "a", "before": "b c"}),
            "b": JobDefinition({"id": "b"}),
        }

        self.ctrl.add_before_deps(job_a, job_map, global_job_map)
        mock_logger.debug.assert_called_once_with(
            "Job a has a before dependency on b which is not in the current "
            "test plan"
        )
        mock_logger.error.assert_called_once_with(
            "Job a has a before dependency on c which does not exist"
        )

    def test_get_inhibitor_list_PENDING_RESOURCE(self):
        # verify that jobs that require a resource that hasn't been
        # invoked yet produce the PENDING_RESOURCE inhibitor
        j1 = JobDefinition({"id": "j1", "requires": 'j2.attr == "ok"'})
        j2 = JobDefinition({"id": "j2"})
        session_state = mock.MagicMock(spec=SessionState)
        session_state.job_state_map["j2"].job = j2
        session_state.resource_map = {}
        self.assertEqual(
            self.ctrl.get_inhibitor_list(session_state, j1),
            [
                JobReadinessInhibitor(
                    InhibitionCause.PENDING_RESOURCE,
                    j2,
                    ResourceExpression('j2.attr == "ok"'),
                )
            ],
        )

    def test_get_inhibitor_list_FAILED_RESOURCE(self):
        # verify that jobs that require a resource that has been
        # invoked and produced resources but the expression dones't
        # evaluate to True produce the FAILED_RESOURCE inhibitor
        j1 = JobDefinition({"id": "j1", "requires": 'j2.attr == "ok"'})
        j2 = JobDefinition({"id": "j2"})
        session_state = mock.MagicMock(spec=SessionState)
        session_state.job_state_map["j2"].job = j2
        session_state.resource_map = {"j2": [Resource({"attr": "not-ok"})]}
        self.assertEqual(
            self.ctrl.get_inhibitor_list(session_state, j1),
            [
                JobReadinessInhibitor(
                    InhibitionCause.FAILED_RESOURCE,
                    j2,
                    ResourceExpression('j2.attr == "ok"'),
                )
            ],
        )

    def test_get_inhibitor_list_good_resource(self):
        # verify that jobs that require a resource that has been invoked and
        # produced resources for which the expression evaluates to True don't
        # have any inhibitors
        j1 = JobDefinition({"id": "j1", "requires": 'j2.attr == "ok"'})
        j2 = JobDefinition({"id": "j2"})
        session_state = mock.MagicMock(spec=SessionState)
        session_state.resource_map = {"j2": [Resource({"attr": "ok"})]}
        session_state.job_state_map["j2"].job = j2
        self.assertEqual(self.ctrl.get_inhibitor_list(session_state, j1), [])

    def test_get_inhibitor_list_PENDING_DEP(self):
        # verify that jobs that depend on another job or wait (via after) for
        # another  that hasn't been invoked yet produce the PENDING_DEP
        # inhibitor
        j1 = JobDefinition(
            {
                "id": "j1",
                "depends": "j2",
                "after": "j3",
            }
        )
        j2 = JobDefinition({"id": "j2"})
        j3 = JobDefinition({"id": "j3"})
        session_state = mock.MagicMock(spec=SessionState)
        session_state.job_state_map = {
            "j1": mock.Mock(spec_set=JobState),
            "j2": mock.Mock(spec_set=JobState),
            "j3": mock.Mock(spec_set=JobState),
        }
        jsm_j2 = session_state.job_state_map["j2"]
        jsm_j2.job = j2
        jsm_j2.result.outcome = IJobResult.OUTCOME_NONE
        jsm_j3 = session_state.job_state_map["j3"]
        jsm_j3.job = j3
        jsm_j3.result.outcome = IJobResult.OUTCOME_NONE
        self.assertEqual(
            self.ctrl.get_inhibitor_list(session_state, j1),
            [
                JobReadinessInhibitor(InhibitionCause.PENDING_DEP, j2, None),
                JobReadinessInhibitor(InhibitionCause.PENDING_DEP, j3, None),
            ],
        )

    def test_get_inhibitor_list_FAILED_DEP(self):
        # verify that jobs that depend on another job that ran but
        # didn't result in OUTCOME_PASS produce the FAILED_DEP
        # inhibitor.
        j1 = JobDefinition(
            {
                "id": "j1",
                "depends": "j2",
                "after": "j3",
            }
        )
        j2 = JobDefinition({"id": "j2"})
        j3 = JobDefinition({"id": "j3"})
        session_state = mock.MagicMock(spec=SessionState)
        session_state.job_state_map = {
            "j1": mock.Mock(spec_set=JobState),
            "j2": mock.Mock(spec_set=JobState),
            "j3": mock.Mock(spec_set=JobState),
        }
        jsm_j2 = session_state.job_state_map["j2"]
        jsm_j2.job = j2
        jsm_j2.result.outcome = IJobResult.OUTCOME_FAIL
        jsm_j3 = session_state.job_state_map["j3"]
        jsm_j3.job = j3
        jsm_j3.result.outcome = IJobResult.OUTCOME_FAIL
        self.assertEqual(
            self.ctrl.get_inhibitor_list(session_state, j1),
            [JobReadinessInhibitor(InhibitionCause.FAILED_DEP, j2, None)],
        )

    def test_get_inhibitor_list_NOT_FAILED_DEP(self):
        # verify that jobs that depend on another job that ran but
        # didn't result in OUTCOME_FAIL produce the NOT_FAILED_DEP
        # inhibitor.
        j1 = JobDefinition(
            {
                "id": "j1",
                "salvages": "j2",
            }
        )
        j2 = JobDefinition({"id": "j2"})
        session_state = mock.MagicMock(spec=SessionState)
        session_state.job_state_map = {
            "j1": mock.Mock(spec_set=JobState),
            "j2": mock.Mock(spec_set=JobState),
        }
        jsm_j2 = session_state.job_state_map["j2"]
        jsm_j2.job = j2
        jsm_j2.result.outcome = IJobResult.OUTCOME_NONE
        self.assertEqual(
            self.ctrl.get_inhibitor_list(session_state, j1),
            [JobReadinessInhibitor(InhibitionCause.NOT_FAILED_DEP, j2, None)],
        )

    def test_get_inhibitor_list_good_dep(self):
        # verify that jobs that depend on another job that ran and has outcome
        # equal to OUTCOME_PASS don't have any inhibitors
        j1 = JobDefinition({"id": "j1", "depends": "j2", "after": "j3"})
        j2 = JobDefinition({"id": "j2"})
        j3 = JobDefinition({"id": "j3"})
        session_state = mock.MagicMock(spec=SessionState)
        session_state.job_state_map = {
            "j1": mock.Mock(spec_set=JobState),
            "j2": mock.Mock(spec_set=JobState),
            "j3": mock.Mock(spec_set=JobState),
        }
        jsm_j2 = session_state.job_state_map["j2"]
        jsm_j2.job = j2
        jsm_j2.result.outcome = IJobResult.OUTCOME_PASS
        jsm_j3 = session_state.job_state_map["j3"]
        jsm_j3.job = j3
        jsm_j3.result.outcome = IJobResult.OUTCOME_PASS
        self.assertEqual(self.ctrl.get_inhibitor_list(session_state, j1), [])

    def test_get_inhibitor_list__suspend_job(self):
        j1 = JobDefinition(
            {
                "id": "j1",
                "flags": Suspend.AUTO_FLAG,
            }
        )
        j2 = JobDefinition(
            {
                "id": "j2",
            }
        )
        suspend_job = JobDefinition({"id": Suspend.AUTO_JOB_ID})
        session_state = mock.MagicMock(spec=SessionState)
        session_state.job_state_map = {
            "j1": mock.Mock(spec_set=JobState),
            "j2": mock.Mock(spec_set=JobState),
            Suspend.AUTO_JOB_ID: mock.Mock(spec_set=JobState),
        }
        jsm_j1 = session_state.job_state_map["j1"]
        jsm_j1.job = j1
        jsm_j1.result.outcome = IJobResult.OUTCOME_NONE
        jsm_j1.readiness_inhibitor_list = []
        jsm_j2 = session_state.job_state_map["j2"]
        jsm_j2.job = j2
        jsm_j2.result.outcome = IJobResult.OUTCOME_NONE
        jsm_j2.readiness_inhibitor_list = []
        jsm_suspend = session_state.job_state_map[Suspend.AUTO_JOB_ID]
        jsm_suspend.job = suspend_job
        jsm_suspend.result.outcome = IJobResult.OUTCOME_NONE
        jsm_suspend.readiness_inhibitor_list = []
        self.assertEqual(
            self.ctrl.get_inhibitor_list(session_state, suspend_job),
            [JobReadinessInhibitor(InhibitionCause.PENDING_DEP, j1, None)],
        )

    def test_is_job_impacting_suspend__wrong_suspend_job(self):
        job = JobDefinition(
            {
                "id": "job",
            }
        )
        self.assertEqual(
            self.ctrl._is_job_impacting_suspend("wrong-suspend-job-id", job),
            False,
        )

    def test_is_job_impacting_suspend__flag(self):
        job = JobDefinition(
            {
                "id": "job",
                "flags": "also-after-suspend",
            }
        )
        self.assertEqual(
            self.ctrl._is_job_impacting_suspend(Suspend.AUTO_JOB_ID, job), True
        )
        self.assertEqual(
            self.ctrl._is_job_impacting_suspend(Suspend.MANUAL_JOB_ID, job),
            False,
        )

    def test_is_job_impacting_suspend__siblings(self):
        job = JobDefinition(
            {
                "id": "job",
                "siblings": json.dumps(
                    [
                        {
                            "id": "sibling-j1",
                            "depends": Suspend.MANUAL_JOB_ID,
                        }
                    ]
                ),
            }
        )
        self.assertEqual(
            self.ctrl._is_job_impacting_suspend(Suspend.AUTO_JOB_ID, job),
            False,
        )
        self.assertEqual(
            self.ctrl._is_job_impacting_suspend(Suspend.MANUAL_JOB_ID, job),
            True,
        )

    def test_observe_result__normal(self):
        job = mock.Mock(spec=JobDefinition)
        result = mock.Mock(spec=IJobResult)
        session_state = mock.MagicMock(spec=SessionState)
        self.ctrl.observe_result(session_state, job, result)
        # Ensure that result got stored
        self.assertIs(session_state.job_state_map[job.id].result, result)
        # Ensure that signals got fired
        session_state.on_job_state_map_changed.assert_called_once_with()
        session_state.on_job_result_changed.assert_called_once_with(
            job, result
        )

    def test_observe_result__OUTCOME_NONE(self):
        job = mock.Mock(spec=JobDefinition, plugin="resource")
        result = mock.Mock(spec=IJobResult, outcome=IJobResult.OUTCOME_NONE)
        session_state = mock.MagicMock(spec=SessionState)
        self.ctrl.observe_result(session_state, job, result)
        # Ensure that result got stored
        self.assertIs(session_state.job_state_map[job.id].result, result)
        # Ensure that signals got fired
        session_state.on_job_state_map_changed.assert_called_once_with()
        session_state.on_job_result_changed.assert_called_once_with(
            job, result
        )
        # Ensure that a resource was *not* defined
        self.assertEqual(session_state.set_resource_list.call_count, 0)

    def test_observe_result__resource(self):
        job = mock.Mock(spec=JobDefinition, plugin="resource")
        result = mock.Mock(spec=IJobResult, outcome=IJobResult.OUTCOME_PASS)
        result.get_io_log.return_value = [
            (0, "stdout", b"attr: value1\n"),
            (0, "stdout", b"\n"),
            (0, "stdout", b"attr: value2\n"),
        ]
        session_state = mock.MagicMock(spec=SessionState)
        self.ctrl.observe_result(session_state, job, result)
        # Ensure that result got stored
        self.assertIs(session_state.job_state_map[job.id].result, result)
        # Ensure that signals got fired
        session_state.on_job_state_map_changed.assert_called_once_with()
        session_state.on_job_result_changed.assert_called_once_with(
            job, result
        )
        # Ensure that new resource was defined
        session_state.set_resource_list.assert_called_once_with(
            job.id,
            [Resource({"attr": "value1"}), Resource({"attr": "value2"})],
        )

    @mock.patch("plainbox.impl.ctrl.logger")
    def test_observe_result__broken_resource(self, mock_logger):
        job = mock.Mock(spec=JobDefinition, plugin="resource")
        result = mock.Mock(spec=IJobResult, outcome=IJobResult.OUTCOME_PASS)
        result.get_io_log.return_value = [(0, "stdout", b"barf\n")]
        session_state = mock.MagicMock(spec=SessionState)
        self.ctrl.observe_result(session_state, job, result)
        # Ensure that result got stored
        self.assertIs(session_state.job_state_map[job.id].result, result)
        # Ensure that signals got fired
        session_state.on_job_state_map_changed.assert_called_once_with()
        session_state.on_job_result_changed.assert_called_once_with(
            job, result
        )
        # Ensure that a warning was logged
        mock_logger.warning.assert_called_once_with(
            "local script %s returned invalid RFC822 data: %s",
            job.id,
            RFC822SyntaxError(None, 1, "Unexpected non-empty line: 'barf\\n'"),
        )

    @mock.patch("plainbox.impl.ctrl.logger")
    def test_observe_result__missing_resource_key(self, mock_logger):
        job = make_job("R", plugin="resource")
        template = TemplateUnit(
            {
                "template-resource": job.id,
                "id": "foo-{missing}",
                "plugin": "shell",
            }
        )
        result = mock.Mock(spec=IJobResult, outcome=IJobResult.OUTCOME_PASS)
        result.get_io_log.return_value = [
            (0, "stdout", b"attr: value1\n"),
            (0, "stdout", b"\n"),
            (0, "stdout", b"attr: value2\n"),
        ]
        session_state = SessionState([template, job])
        self.ctrl.observe_result(session_state, job, result)
        # Ensure that a warning was logged
        mock_logger.critical.assert_called_with(
            "Ignoring %s with missing template parameter %s",
            "foo-{missing}",
            "missing",
        )

    @mock.patch("plainbox.impl.ctrl.logger")
    def test_observe_result__missing_resource_key_invalid_units(
        self, mock_logger
    ):
        job = make_job("R", plugin="resource")
        template = TemplateUnit(
            {
                "template-resource": job.id,
                "id": "foo-{missing}",
                "plugin": "shell",
            }
        )
        result = mock.Mock(spec=IJobResult, outcome=IJobResult.OUTCOME_PASS)
        result.get_io_log.return_value = [
            (0, "stdout", b"attr: value1\n"),
            (0, "stdout", b"\n"),
            (0, "stdout", b"attr: value2\n"),
        ]
        session_state = SessionState([template, job])
        session_state.metadata.flags.add(
            session_state.metadata.FLAG_FEATURE_STRICT_TEMPLATE_EXPANSION
        )
        self.ctrl.observe_result(session_state, job, result)

        job_ids = set(session_state.job_state_map.keys())
        job_ids.remove("R")
        # 2 resource outputs will generate 2 jobs
        self.assertEqual(len(job_ids), 2)
        self.assertTrue(all(job_id.startswith("foo-") for job_id in job_ids))

    @mock.patch("plainbox.impl.ctrl.logger")
    def test__filter_invalid_log_valid(self, mock_logger):
        unit_warning = mock.MagicMock(severity=Severity.warning)
        valid_unit = mock.MagicMock()
        valid_unit.check.return_value = [unit_warning]
        kept = CheckBoxSessionStateController._filter_invalid_log(valid_unit)

        self.assertTrue(mock_logger.warning.called)
        # unit is valid, has to be kept
        self.assertTrue(kept)

    @mock.patch("plainbox.impl.ctrl.logger")
    def test__filter_invalid_log_error(self, mock_logger):
        unit_warning = mock.MagicMock(severity=Severity.warning)
        unit_error = mock.MagicMock(severity=Severity.error)
        invalid_unit = mock.MagicMock()
        invalid_unit.check.return_value = [unit_warning, unit_error]
        kept = CheckBoxSessionStateController._filter_invalid_log(invalid_unit)

        self.assertTrue(mock_logger.warning.called)
        # unit contains at least 1 error, it has to be discarded
        self.assertFalse(kept)
        # this is dangerous as the invalid unit will be ignored
        self.assertTrue(mock_logger.critical.called)

    @mock.patch("plainbox.impl.ctrl.logger")
    def test__filter_invalid_log_error_missing_paramn(self, mock_logger):
        invalid_unit = mock.MagicMock()
        invalid_unit.check.side_effect = MissingParam(
            "template_123", "id", "generated_unit_id{abc}", "abc"
        )
        kept = CheckBoxSessionStateController._filter_invalid_log(invalid_unit)

        # unit contains at least 1 error, it has to be discarded
        self.assertFalse(kept)
        # this is dangerous as the invalid unit will be ignored
        self.assertTrue(mock_logger.critical.called)

    @mock.patch("plainbox.impl.ctrl.logger")
    def test__wrap_invalid_units_valid(self, mock_logger):
        unit_warning = mock.MagicMock(severity=Severity.warning)
        valid_unit = mock.MagicMock()
        valid_unit.check.return_value = [unit_warning]
        wrapped = CheckBoxSessionStateController._wrap_invalid_units(
            valid_unit
        )

        self.assertTrue(mock_logger.warning.called)
        # unit is valid, has should return the unit unchanged
        self.assertEqual(wrapped, valid_unit)

    @mock.patch("plainbox.impl.ctrl.logger")
    def test__wrap_invalid_units_errors(self, mock_logger):
        unit_warning = mock.MagicMock(severity=Severity.warning)
        unit_error = mock.MagicMock(severity=Severity.error)
        invalid_unit = mock.MagicMock()
        invalid_unit.check.return_value = [unit_warning, unit_error]
        wrapped = CheckBoxSessionStateController._wrap_invalid_units(
            invalid_unit
        )

        self.assertTrue(mock_logger.warning.called)
        # unit contains at least 1 error, has to be wrapped to make reporting
        # possible
        self.assertIsInstance(wrapped, InvalidJob)

    def test__wrap_invalid_units_missing_param(self):
        invalid_unit = mock.MagicMock()
        invalid_unit.check.side_effect = MissingParam(
            "template_123", "id", "generated_unit_id{abc}", "abc"
        )
        wrapped = CheckBoxSessionStateController._wrap_invalid_units(
            invalid_unit
        )

        # unit contains at least 1 error, it has to be discarded
        self.assertIsInstance(wrapped, InvalidJob)


class FunctionTests(TestCase):
    """
    unit tests for gen_rfc822_records_from_io_log() and other functions.
    """

    def test_parse_typical(self):
        """
        verify typical operation without any parsing errors
        """
        # Setup a mock job and result, give some io log to the result
        job = mock.Mock(spec=JobDefinition)
        result = mock.Mock(spec=IJobResult)
        result.get_io_log.return_value = [
            (0, "stdout", b"attr: value1\n"),
            (0, "stdout", b"\n"),
            (0, "stdout", b"attr: value2\n"),
        ]
        # Parse the IO log records
        records = list(gen_rfc822_records_from_io_log(job, result))
        # Ensure that we saw both records
        self.assertEqual(
            records,
            [
                RFC822Record(
                    {"attr": "value1"}, Origin(JobOutputTextSource(job), 1, 1)
                ),
                RFC822Record(
                    {"attr": "value2"}, Origin(JobOutputTextSource(job), 3, 3)
                ),
            ],
        )

    @mock.patch("plainbox.impl.ctrl.logger")
    def test_parse_error(self, mock_logger):
        # Setup a mock job and result, give some io log to the result
        job = mock.Mock(spec=JobDefinition)
        result = mock.Mock(spec=IJobResult)
        result.get_io_log.return_value = [
            (0, "stdout", b"attr: value1\n"),
            (0, "stdout", b"\n"),
            (0, "stdout", b"error\n"),
            (0, "stdout", b"\n"),
            (0, "stdout", b"attr: value2\n"),
        ]
        # Parse the IO log records
        records = list(gen_rfc822_records_from_io_log(job, result))
        # Ensure that only the first record was generated
        self.assertEqual(
            records,
            [
                RFC822Record(
                    {"attr": "value1"}, Origin(JobOutputTextSource(job), 1, 1)
                ),
            ],
        )
        # Ensure that a warning was logged
        mock_logger.warning.assert_called_once_with(
            "local script %s returned invalid RFC822 data: %s",
            job.id,
            RFC822SyntaxError(
                None, 3, "Unexpected non-empty line: 'error\\n'"
            ),
        )


class SymLinkNestTests(TestCase):
    """
    Tests for SymLinkNest class
    """

    NEST_DIR = "nest"

    def setUp(self):
        self.nest = SymLinkNest(self.NEST_DIR)

    def test_init(self):
        """
        verify that SymLinkNest.__init__() stores its argument
        """
        self.assertEqual(self.nest._dirname, self.NEST_DIR)

    def test_add_provider(self):
        """
        verify that add_provider() adds each executable
        """
        provider = mock.Mock(name="provider", spec=Provider1)
        provider.executable_list = ["exec1", "exec2"]
        with mock.patch.object(self.nest, "add_executable"):
            self.nest.add_provider(provider)
            self.nest.add_executable.assert_has_calls(
                [(("exec1",), {}), (("exec2",), {})]
            )

    @mock.patch("os.symlink")
    def test_add_executable(self, mock_symlink):
        self.nest.add_executable("/usr/lib/foo/exec")
        mock_symlink.assert_called_with("/usr/lib/foo/exec", "nest/exec")
