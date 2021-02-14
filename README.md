# drone-alertmanager

[![Build Status](https://drone.osshelp.ru/api/badges/drone/drone-alertmanager/status.svg)](https://drone.osshelp.ru/drone/drone-alertmanager)

Creates and removes silences in AlertManager.

## Usage

### Modes (action)

- `create` - create silence
- `delete` - remove silence

### Other settings

- `urls` - list of urls to send a request
- `duration` - duration of added silence (only for create action)
- `template` - one of the available request body templates
- `strict_match` - checking for an exact match of all alert conditions (false by default)
- `valid_response_codes` - list of expected HTTP response codes
- `headers` - additional request headers
- `custom_template` - arbitrary request body (Jinja syntax with access to environment variables is supported)
- `username/password` - credentials for HTTP authorization
- `skip_verify` - disable SSL certificate verification (for self-signed ones)
- `follow_redirects` - follow a 301/302 redirect
- `timeout` - maximum time to wait for a response (in seconds)

## Usage examples

An example of use in a build step for our "typical" container deployment:

``` yaml
- name: add silence
    image: osshelp/drone-alertmanager
    settings:
      urls:
        - https://clientname.ossmon.ru/alertmanager
      action: create
      template: default
      duration: 600
      job: '^clientname$'
      instance: '^server-\w+.+'

    <# Deploy steps #>

  - name: remove silence
    image: osshelp/drone-alertmanager
    settings:
      urls:
        - https://clientname.ossmon.ru/alertmanager
      action: delete
    when:
      status:
        - success
        - failure
```

## Templates

### default

So far, the main and only template.

``` json
{
  "id": null,
  "createdBy": "drone/alertmanager",
  "startsAt": "2019-11-14T02:27:07.429780Z",
  "endsAt": "2019-11-14T02:29:07.429780Z",
  "comment": "Created for build#42 of orgname/reponame, see http://clientname.ossbuild.ru/link/to/build",
  "matchers": [
  {
    "isRegex": true,
    "name": "job",
    "value": "^clientname$" 
  },
  {
    "isRegex": true,
    "name": "instance",
    "value": "^server-\\w+.+" 
  }
]
}
```

### Internal usage

For internal purposes and OSSHelp customers we have an alternative image url:

``` yaml
  image: oss.help/drone/alertmanager
```

There is no difference between the DockerHub image and the oss.help/drone image.

## Links

- [Our article](https://oss.help/kb4211)

## TODO:

- skip adding silence if exactly the "same" already exists (i.e. same labels)
- what else do we need to "escape" for regexp in JSON?
- prepare typical regexp as examples and recommendations for commonly used labels
