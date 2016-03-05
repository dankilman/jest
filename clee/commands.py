########
# Copyright (c) 2016 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
############

import argparse
import datetime
import sys

import yaml
import colors
import argh
from argh.decorators import arg
from path import path

from clee.jenkins import jenkins
from clee.cache import cache
from clee.configuration import configuration
from clee.completion import completion


app = argh.EntryPoint('clee')
command = app


@command
@arg('--jenkins-username', required=True)
@arg('--jenkins-password', required=True)
@arg('--jenkins-base_url', required=True)
def init(jenkins_username=None,
         jenkins_password=None,
         jenkins_base_url=None,
         jenkins_system_tests_base=None,
         reset=False):
    configuration.save(jenkins_username=jenkins_username,
                       jenkins_password=jenkins_password,
                       jenkins_base_url=jenkins_base_url,
                       jenkins_system_tests_base=jenkins_system_tests_base,
                       reset=reset)
    cache.clear()


@command
def list_jobs():
    jobs = jenkins.list_jobs()
    for job in jobs['jobs']:
        print job.get('name')


@command
@argh.named('list')
@arg('job', completer=completion.job_completer)
def ls(job):
    builds = jenkins.list_builds(job)
    for build in builds:
        result = build['result']
        building = build['building']
        number = str(build['number'])
        cause = build['cause']
        timestamp = build['timestamp']
        build_datetime = datetime.datetime.fromtimestamp(timestamp / 1000.0)
        build_datetime = build_datetime.strftime('%Y-%m-%d %H:%M:%S')

        if building:
            build_color = colors.white
            result = 'BUILDING'
        elif result == 'FAILURE':
            build_color = colors.red
        elif result == 'ABORTED':
            build_color = colors.yellow
        else:
            build_color = colors.green

        print '{:<4}{:<18}{} ({})'.format(number,
                                          build_color(result),
                                          cause,
                                          build_datetime)


@command
@arg('job', completer=completion.job_completer)
@arg('build', completer=completion.build_completer)
def status(job, build, failed=False, output_files=False):
    build_number = build
    build = jenkins.fetch_build(job, build)
    if build['build'].get('building'):
        return 'Building is currently running'
    report = build['test_report']
    if report.get('status') == 'error':
        return 'No tests report has been generated for this build'
    files_dir = _files_dir(job, build_number)
    failed_dir = files_dir / 'failed'
    passed_dir = files_dir / 'passed'
    if output_files:
        files_dir.mkdir_p()
        for d in [passed_dir, failed_dir]:
            d.rmtree_p()
            d.mkdir()
    for suite in report['suites']:
        suite_name = suite['name']
        cases = []
        has_passed = False
        has_failed = False
        for case in suite['cases']:
            test_status = case['status']
            if test_status in ['FAILED', 'REGRESSION']:
                test_status = 'FAILED'
                colored_status = colors.red(test_status)
                has_failed = True
            elif test_status in ['PASSED', 'FIXED']:
                test_status = 'PASSED'
                colored_status = colors.green(test_status)
                has_passed = True
            elif test_status == 'SKIPPED':
                colored_status = colors.yellow(test_status)
                has_failed = True
            else:
                colored_status = test_status
            name = case['name']
            if not failed or test_status != 'PASSED':
                cases.append('{:<18}{}'.format(
                    colored_status,
                    name.split('@')[0]))
            if output_files:
                filename = name.replace(' ', '-')
                dirname = passed_dir if test_status == 'PASSED' else failed_dir
                with open(dirname / filename, 'w') as f:
                    f.write('name: {}\n\n'.format(case['name']))
                    f.write('status: {}\n\n'.format(case['status']))
                    f.write('class: {}\n\n'.format(case['className']))
                    f.write('duration: {}\n\n'.format(case['duration']))
                    f.write('error details: {}\n\n'.format(
                        case['errorDetails']))
                    f.write('error stacktrace: {}\n\n'.format(
                        case['errorStackTrace']))
                    f.write('stdout: \n{}\n\n'.format(case['stdout']))
                    f.write('stderr: \b{}\n\n'.format(case['stderr']))
        if has_passed and has_failed:
            suite_name_color = colors.yellow
        elif has_passed:
            suite_name_color = colors.green
        elif has_failed:
            suite_name_color = colors.red
        else:
            suite_name_color = colors.white
        if cases:
            print suite_name_color(colors.bold(suite_name))
            print suite_name_color(colors.bold('-' * (len(suite_name))))
            print '\n'.join(cases)
            print
    if output_files:
        print 'Output files written to {}'.format(files_dir)


@command
@arg('job', completer=completion.job_completer)
@arg('builds',
     completer=completion.build_completer,
     nargs=argparse.ONE_OR_MORE)
def analyze(job, builds, passed_at_least_once=False, failed=False):
    build_numbers = set()
    for build in builds:
        split = build.split('-')
        if len(split) > 2:
            raise argh.CommandError('Illegal build range: {}'.format(build))
        elif len(split) == 1:
            build_numbers.add(build)
        else:
            start, stop = int(split[0]), int(split[1])
            build_numbers |= set(str(i) for i in range(start, stop+1))
    builds = [jenkins.fetch_build(job, b) for b in build_numbers]
    report = {}
    for build in builds:
        if build['build'].get('building'):
            print 'Skipping build {} as it currently running'.format(
                    build['number'])
            continue
        test_report = build['test_report']
        if test_report.get('status') == 'error':
            print 'Skipping build {} as no test reports were generated for it'\
                .format(build['number'])
        for suite in test_report['suites']:
            suite_name = suite['name']
            report_suite = report.get(suite_name, {})
            for case in suite['cases']:
                case_name = case['name'].split('@')[0]
                test_status = case['status']
                if test_status in ['FAILED', 'REGRESSION']:
                    test_status = 'FAILED'
                elif test_status in ['PASSED', 'FIXED']:
                    test_status = 'PASSED'
                report_case = report_suite.get(case_name, {})
                report_case_status = report_case.get(test_status, 0)
                report_case_status += 1
                report_case[test_status] = report_case_status
                report_suite[case_name] = report_case
            report[suite_name] = report_suite
    for suite_name, suite in report.items():
        cases = []
        suite_has_passed = False
        suite_has_failed = False
        for case_name, case in sorted(suite.items()):
            pass_count = case.get('PASSED', 0)
            fail_count = case.get('FAILED', 0)
            skip_count = case.get('SKIPPED', 0)
            case_has_failed = False
            if pass_count and (fail_count or skip_count) \
                    and not passed_at_least_once:
                case_color = colors.yellow
                suite_has_failed = case_has_failed = True
            elif pass_count:
                case_color = colors.green
                suite_has_passed = True
            else:
                case_color = colors.red
                suite_has_failed = case_has_failed = True
            if not failed or case_has_failed:
                cases.append('{} [passed={}, failed={}, skipped={}]'.
                             format(case_color(case_name),
                                    pass_count, fail_count, skip_count))
        if suite_has_passed and suite_has_failed:
            suite_name_color = colors.yellow
        elif suite_has_passed:
            suite_name_color = colors.green
        elif suite_has_failed:
            suite_name_color = colors.red
        else:
            suite_name_color = colors.white
        if cases:
            print suite_name_color(colors.bold(suite_name))
            print suite_name_color(colors.bold('-' * (len(suite_name))))
            print '\n'.join(cases)
            print


@command
@arg('job', completer=completion.job_completer)
@arg('build', completer=completion.build_completer)
def logs(job, build, stdout=False, tail=False):
    if not stdout:
        files_dir = _files_dir(job, build)
        files_dir.mkdir_p()
        log_path = files_dir / 'console.log'
    else:
        log_path = None

    if not tail:
        result = jenkins.fetch_build_logs(job, build)
        if stdout:
            return result
        else:
            log_path.write_text(result, encoding='utf8')
            print 'Log file written to {}'.format(log_path)
    else:
        if stdout:
            stream = sys.stdout
        else:
            stream = open(log_path, 'w')
        for chunk in jenkins.tail_build_logs(job, build):
            stream.write(chunk.encode(encoding='utf8'))
            stream.flush()
        if not stdout:
            stream.close()


@command
@arg('job', completer=completion.job_completer)
def build(job, branch=None, descriptor=None, source=None):
    parameters = {}
    if source:
        source_path = path(source).expanduser()
        if source_path.exists():
            parameters = yaml.safe_load(source_path.text())
        else:
            try:
                source = int(source)
            except ValueError:
                raise argh.CommandError('Invalid source: {}'.format(source))
            fetched_build = jenkins.fetch_build(job, source)
            actions = fetched_build['build']['actions']
            for action in actions:
                action_parameters = action.get('parameters')
                if not action_parameters:
                    continue
                if not any([parameter.get('name') == 'system_tests_branch'
                            for parameter in action_parameters]):
                    continue
                parameters = {p['name']: p['value'] for p in action_parameters}
                break
            else:
                raise argh.CommandError('Invalid build: {}'.format(source))
    if branch:
        parameters['system_tests_branch'] = branch
    if descriptor:
        parameters['system_tests_descriptor'] = descriptor
    jenkins.build_job(job, parameters=parameters)
    print 'Build successfully queued [job={}, parameters={}]'.format(
        job, parameters)


@command
def clear_cache():
    cache.clear()


def _files_dir(job, build):
    name = '{}-{}'.format(job, build)
    files_dir = path('.').abspath()
    while files_dir.dirname() != files_dir:
        if files_dir.basename() == name:
            return files_dir
        files_dir = files_dir.dirname()
    return path(name).abspath()
