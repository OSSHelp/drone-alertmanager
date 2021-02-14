#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Drone CI plugin for AlertManager."""

import datetime
import json
import os
import re
import sys

import httplib2

from jinja2 import BaseLoader, Environment, FileSystemLoader

# default settings
debug_mode = False
supported_actions = ["create", "delete"]
supported_build_events = ["push", "tag", "cron", "pull_request",
                          "promote", "rollback", "custom"]

default_charset = 'UTF-8'
plugin_user_agent = "drone/alertmanager"
request_headers = {
    "Content-Type": "application/json; charset = UTF-8",
    "User-Agent": plugin_user_agent
}

required_env = [
    'DRONE_REPO_OWNER', 'DRONE_REPO_NAME',
    'DRONE_BUILD_LINK', 'DRONE_BUILD_NUMBER',
    'DRONE_BUILD_STARTED', 'DRONE_BUILD_FINISHED',
    'DRONE_BUILD_EVENT', 'DRONE_STAGE_NAME',
    'DRONE_BUILD_NUMBER'
]


def strip_version(input):
    """Strip v prefix from vX.Y.Z tags."""
    if re.match(r"^v[0-9]+\.[0-9]+\.[0-9]+", input):
        return input[1:]
    else:
        return input


def escape_for_json(input):
    return input.replace("\\", "\\\\")


def mandatory(input, msg=None):
    """Check if Jinja's variable if defined."""
    from jinja2.runtime import Undefined
    from jinja2.exceptions import TemplateRuntimeError

    ''' Make a variable mandatory '''
    if isinstance(input, Undefined):
        if input._undefined_name is not None:
            name = "%s" % str(input._undefined_name)
        else:
            name = ''
        if msg is None:
            if name[:7] == 'PLUGIN_':
                msg = "Mandatory option \"{0}\" is not defined in plugin settings".format(name[7:].lower())
            else:
                msg = "Mandatory variable \"{0}\" is not defined via environment".format(name)
        raise TemplateRuntimeError(msg)

    return input


def print_msg(level, message):
    """Printing notice message."""
    now = datetime.datetime.today().strftime("%Y/%m/%d-%H:%M:%S")
    if level.lower() == 'notice':
        print("\033[34m[{0} {1}]\033[39m {2}".format(level.upper(), now, message))
    elif level.lower() == 'warning':
        print("\033[33m[{0} {1}]\033[39m {2}".format(level.upper(), now, message))
    elif level.lower() == 'error':
        print("\033[31m[{0} {1}]\033[39m {2}".format(level.upper(), now, message))
    else:
        print("[{0} {1}] {2}".format(level.upper(), now, message))


def fatal_error(message):
    """Printing notice message."""
    print_msg("ERROR", message)
    sys.exit(1)


def in_env(key):
    """Check for presence of non-empty variable in environment."""
    return from_env(key) not in ['', None]


def from_env(key, default=''):
    """Read variable from environment or return default."""
    return os.environ.get(key, default)


def timestamp_diff(start, end):
    """Converting seconds to human readble period."""
    time_diff = int(end) - int(start)
    return str(datetime.timedelta(seconds=time_diff))


def time_in_rfc3339(target, delta=None):
    """Formatting time for AlertManager"""
    if delta is None:
        return target.isoformat("T") + "Z"
    else:
        return (target + datetime.timedelta(seconds=int(delta))).isoformat("T") + "Z"


def silence_author():
    """Prepare string for createdBy field in silence."""
    return '{0}'.format(plugin_user_agent)


def silence_comment():
    """Prepare string for comment field in silence."""
    return 'Created for build#{0} of {1}/{2}, see {3}'.format(
        from_env('DRONE_BUILD_NUMBER'), from_env('DRONE_REPO_OWNER'),
        from_env('DRONE_REPO_NAME'), from_env('DRONE_BUILD_LINK'))


def decode_and_parse_json(content):
    """Uncompress and parse JSON from response."""
    result = []
    try:
        result = json.loads(content.decode(default_charset))
    except Exception as e:
        print_msg("ERROR", "Failed decode and parse response:\n{0}".format(e))

    return result


def find_silences(base_url):
    """Search for matching non-expired silences."""
    silence_ids = []
    request_method = 'GET'
    target_url = base_url + '/api/v2/silences'
    required_silence_author = silence_author()
    required_silence_comment = silence_comment()

    if strict_match:
        payload_template = from_env('PLUGIN_TEMPLATE', 'default') + '/{0}'.format('_matchers')
        required_matchers = json.loads('{' + render_template(payload_template) + '}')

    print_msg("NOTICE", "Searching for non-expired silences (strict_match={0})".format(str(strict_match).lower()))

    response, content = make_request(target_url, request_method)
    if response['content-type'] != 'application/json':
        print_msg("WARNING", "Got \"{0}\" as Content-Type type instead of expected \"application/json\"".format(response['content-type']))
        return None
    try:
        silences_list = decode_and_parse_json(content)
        for target_silence in silences_list:
            if target_silence['status']['state'] == 'expired':
                continue
            if target_silence['createdBy'] != required_silence_author:
                continue
            if target_silence['comment'] != required_silence_comment:
                continue
            if not strict_match or (strict_match and target_silence['matchers'] == required_matchers['matchers']):
                print_msg("DEBUG", "Found matching silence with ID {0}".format(target_silence['id']))
                silence_ids.append(target_silence['id'])
    except Exception as e:
        print_msg("ERROR", "Searching for silence failed with an error:\n{0}".format(e))

    return silence_ids


def replace_macroses(raw_payload):
    """Replace supported macroses in payload."""
    global build_event

    if '{{deploy_target}}' not in raw_payload:
        return raw_payload

    # check for supported events
    if build_event not in ['promote', 'rollback']:
        fatal_error("Tag \"{{deploy_target}}\" is available only for promote and rollback events")
    # in case of promote/rollback events
    deploy_target = from_env('DRONE_DEPLOY_TO', None)
    # we need non-empty value
    if deploy_target in [None, '']:
        fatal_error("Can't replace \"{{deploy_target}}\", because \"DRONE_DEPLOY_TO\" is empty or unavailable in environment")

    return raw_payload.replace('{{deploy_target}}', deploy_target)


def render_custom_template(custom_template):
    """Render Jinja's template from a string."""
    template = Environment(loader=BaseLoader).from_string(custom_template)
    template.filters['strip_version'] = strip_version
    template.filters['mandatory'] = mandatory
    template.filters['escape_for_json'] = escape_for_json
    env_vars = dict(os.environ.items())
    payload = template.render(env_vars)
    return replace_macroses(payload)


def render_template(target_template):
    """Render Jinja's teamplate from a file."""
    templates_dir = from_env('TEMPLATES_PATH', '/templates/')
    template_file = '{0}{1}.j2'.format(templates_dir, target_template)

    if not (os.path.exists(template_file) and os.path.isfile(template_file)):
        print_msg('ERROR', "Can't read template from {0}".format(template_file))
        sys.exit(1)

    jinja_env = Environment(loader=FileSystemLoader(templates_dir))
    jinja_env.filters['strip_version'] = strip_version
    jinja_env.filters['mandatory'] = mandatory
    jinja_env.filters['escape_for_json'] = escape_for_json
    template = jinja_env.get_template('{0}.j2'.format(target_template))
    env_vars = dict(os.environ.items())
    raw_payload = template.render(env_vars)
    return replace_macroses(raw_payload)


def make_request(target_url, request_method, template_name=None):
    """Sending request via HTTP."""
    request_payload = {}
    valid_response_codes = [200]
    follow_redirects = True
    request_timeout = 60
    skip_verify = False
    auth_username = ''
    auth_password = ''
    custom_headers = {}

    if in_env('DRONE_BUILD_STARTED') and in_env('DRONE_BUILD_FINISHED'):
        os.environ['DRONE_BUILD_TOTALTIME'] = \
            timestamp_diff(from_env('DRONE_BUILD_STARTED'), from_env('DRONE_BUILD_FINISHED'))
    if in_env('DRONE_BUILD_CREATED') and in_env('DRONE_BUILD_STARTED'):
        os.environ['DRONE_BUILD_QUEUEDTIME'] = \
            timestamp_diff(from_env('DRONE_BUILD_CREATED'), from_env('DRONE_BUILD_STARTED'))

    # ugly way to parse headers list, but it should work
    if in_env('PLUGIN_HEADERS'):
        headers_list = from_env('PLUGIN_HEADERS').split(',')
        for header_string in headers_list:
            k, v = header_string.split(':')
            custom_headers[k.strip()] = v.strip()
        request_headers.update(custom_headers)

    if template_name == 'create':
        # custom template should override predefined payload
        try:
            if in_env('PLUGIN_CUSTOM_TEMPLATE'):
                request_payload = render_custom_template(from_env('PLUGIN_CUSTOM_TEMPLATE'))
            elif in_env('PLUGIN_TEMPLATE'):
                payload_template = from_env('PLUGIN_TEMPLATE', 'default') + '/{0}'.format(template_name)
                request_payload = render_template(payload_template)
        except Exception as e:
            print_msg("ERROR", "Template rendering failed with an error:\n{0}".format(e))
            sys.exit(1)

    if in_env('PLUGIN_SKIP_VERIFY'):
        raw_skip_verify = from_env('PLUGIN_SKIP_VERIFY').lower()
        skip_verify = True if raw_skip_verify == 'true' else False

    if in_env('PLUGIN_FOLLOW_REDIRECTS'):
        raw_follow_redirects = from_env('PLUGIN_FOLLOW_REDIRECTS').lower()
        follow_redirects = True if raw_follow_redirects == 'true' else False

    if in_env('PLUGIN_TIMEOUT'):
        request_timeout = int(from_env('PLUGIN_TIMEOUT'))

    http_obj = httplib2.Http(disable_ssl_certificate_validation=skip_verify, timeout=request_timeout)

    # credentials will be sent only on HTTP/401 response
    if in_env('PLUGIN_USERNAME') and in_env('PLUGIN_PASSWORD'):
        auth_username = from_env('PLUGIN_USERNAME')
        auth_password = from_env('PLUGIN_PASSWORD')
        http_obj.add_credentials(auth_username, auth_password)
        http_obj.follow_redirects = follow_redirects

    if debug_mode:
        print_msg("DEBUG", "Target URL is {0}".format(target_url))
        print_msg("DEBUG", "Method is {0}".format(request_method))
        print_msg("DEBUG", "SkipVerify is {0}".format(skip_verify))
        print_msg("DEBUG", "FollowRedirects is {0}".format(follow_redirects))
        print_msg("DEBUG", "Timeout is {0}".format(request_timeout))
        print_msg("DEBUG", "Headers are {0}".format(request_headers))
        print_msg("DEBUG", "Credentials are {0}:{1}".format(auth_username, auth_password))
        print_msg("DEBUG", "Valid codes are {0}".format(valid_response_codes))
        print_msg("DEBUG", "Here is a message:\n{0}".format(request_payload))

        print_msg("NOTICE", "Sending request to {0}".format(target_url))
    try:
        response, content = http_obj.request(
            uri=target_url,
            method=request_method,
            headers=request_headers,
            body=request_payload
        )

        if debug_mode:
            print_msg("DEBUG", "Here is a response:\n{0}".format(response))
            print_msg("DEBUG", "And here is a body of response:\n{0}".format(content))
        if response.status in valid_response_codes:
            print_msg("NOTICE", "Request successfully sent to {0}, we got HTTP/{1}".format(target_url, response.status))
            return response, content
        else:
            print_msg("ERROR", "Request failed with HTTP/{0}, recieved response below:\n{1}".format(response.status, content))
            sys.exit(1)
    except httplib2.RedirectMissingLocation:
        print_msg("ERROR", "A 3xx redirect response code was provided but no Location:"
                           " header was provided to point to the new location.")
    except httplib2.RedirectLimit:
        print_msg("ERROR", "The maximum number of redirections was reached without coming to a final URI.")
    except httplib2.ServerNotFoundError:
        print_msg("ERROR", "Unable to resolve the host name given for URI:\n {0}.".format(target_url))
    except httplib2.RelativeURIError:
        print_msg("ERROR", "A relative, as opposed to an absolute URI, was passed into request().")
    except httplib2.FailedToDecompressContent:
        print_msg("ERROR", "The headers claimed that the content of the response was compressed"
                           " but the decompression algorithm applied to the content failed.")
    except httplib2.socket.timeout:
        print_msg("ERROR", "Timeout exceeded after {0} seconds, request aborted.".format(request_timeout))
    except Exception as e:
        print_msg("ERROR", "Request failed with an error:\n{0}".format(e))


def perform_action(target_action):
    """Main login of plugin, because of action in settings."""

    # fill in author and comments
    os.environ['SILENCE_COMMENT'] = silence_comment()
    os.environ['SILENCE_CREATED_BY'] = silence_author()

    # looping over target URL's
    target_urls = from_env('PLUGIN_URLS').split(',')
    for target_url in target_urls:

        if target_action == 'create':
            request_suffix = '/api/v2/silences'
            request_method = 'POST'
            # format datetime as 2019-11-13T05:09:38.932Z
            time_now = datetime.datetime.utcnow()
            os.environ['SILENCE_STARTS_AT'] = time_in_rfc3339(time_now)
            os.environ['SILENCE_ENDS_AT'] = time_in_rfc3339(time_now, silence_duration)
            # now we're ready to continue
            resulting_url = target_url + request_suffix
            response, content = make_request(resulting_url, request_method, target_action)
            if response['content-type'] != 'application/json':
                print_msg("WARNING", "Got \"{0}\" as Content-Type type instead of expected \"application/json\"".format(response['content-type']))
            else:
                json_response = decode_and_parse_json(content)
                print_msg("NOTICE", "Silence added with ID {0}".format(json_response['silenceID']))

        if target_action == 'delete':
            # we need existing silence ID to proceed
            silence_ids = find_silences(target_url)
            request_method = 'DELETE'

            if silence_ids:
                # now we're ready to continue
                for silence_id in silence_ids:
                    request_suffix = '/api/v2/silence/{0}'.format(silence_id)
                    resulting_url = target_url + request_suffix
                    print_msg("NOTICE", "Deleting silence with ID {0}".format(silence_id))
                    make_request(resulting_url, request_method, target_action)
            else:
                print_msg("WARNING", "Can't find matching silences, probably they're already expired?")


if __name__ == "__main__":

    if in_env('PLUGIN_DEBUG'):
        if from_env('PLUGIN_DEBUG').lower() == 'true':
            debug_mode = True
            print_msg("DEBUG", "Here is our environment:")
            for k, v in os.environ.items():
                print("{0} => {1}".format(k, v))

    if not in_env('PLUGIN_URLS'):
        print_msg("ERROR", "You shoud set AlertManager's URLs via \"urls\" in settings")
        sys.exit(1)

    # look for strict match of "matchers" in silences?
    if from_env('PLUGIN_STRICT_MATCH', 'false').lower() != 'true':
        # someone could modify selector, but it's ok
        strict_match = False
    else:
        # all selectors should be exactly as we created previously
        strict_match = True

    silence_action = from_env('PLUGIN_ACTION')
    if silence_action not in supported_actions:
        print_msg("ERROR", "Parameter \"action\" is missing or wrong value specified")
        sys.exit(1)

    silence_duration = from_env('PLUGIN_DURATION')
    if silence_action == 'create' and silence_duration in ['', None]:
        print_msg("ERROR", "Parameter \"duration\" is missing or wrong value specified")
        sys.exit(1)

    if not (in_env('PLUGIN_TEMPLATE') or in_env('PLUGIN_CUSTOM_TEMPLATE')):
        print_msg("WARNING", "Settings template and custom_template are missing, rendering default")

    for key in required_env:
        if not (key in os.environ):
            print_msg('ERROR', "Can't find {0} in environment".format(key))
            sys.exit(1)

    build_event = from_env('DRONE_BUILD_EVENT')

    if build_event not in supported_build_events:
        print_msg("ERROR", "Unknown event: {0}".format(build_event))
        sys.exit(1)

    perform_action(silence_action)

    sys.exit(0)
