"""Declared management collector budget; producer ownership remains external."""
import math


def management_exporter_parameters(value):
    fields = {'connectionLogMaxLineBytes', 'expectedProducerWriterMaxSizeMiB',
              'requestConcurrency', 'memoryRequestMiB', 'memoryLimitMiB',
              'cpuLimitMillicores', 'args', 'profile'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('managementExporter requires the complete explicit collector budget')
    for field in fields - {'args', 'profile'}:
        if type(value[field]) is not int or value[field] <= 0:
            raise ValueError(f'managementExporter.{field} must be a positive integer')
    p = value['profile']
    required = {'baselineContainerMiB', 'processBaselineBytes', 'processPeakBytes',
                'headroomPercent', 'cpuMillicores', 'threads', 'maxLineBytes',
                'diskRecordMaxBytes', 'requestConcurrency', 'evidence'}
    if not isinstance(p, dict) or set(p) != required:
        raise ValueError('managementExporter.profile requires explicit capacity profile reference')
    if not isinstance(p['evidence'], str) or not p['evidence']:
        raise ValueError('managementExporter.profile evidence reference is required')
    for key in required - {'evidence'}:
        if type(p[key]) is not int or p[key] <= 0:
            raise ValueError(f'managementExporter.profile.{key} must be a positive integer')
    line = value['connectionLogMaxLineBytes']
    if not value['expectedProducerWriterMaxSizeMiB'] * 1048576 <= line <= p['maxLineBytes'] < p['diskRecordMaxBytes']:
        raise ValueError('managementExporter producer/reader/profile/disk bounds mismatch')
    if p['processPeakBytes'] < p['processBaselineBytes']:
        raise ValueError('managementExporter declared peak is below declared baseline')
    needed = math.ceil((p['baselineContainerMiB'] * 1048576 + p['processPeakBytes'] - p['processBaselineBytes']) * (100+p['headroomPercent']) / 100)
    if not p['baselineContainerMiB'] <= value['memoryRequestMiB'] <= value['memoryLimitMiB'] or value['memoryLimitMiB'] * 1048576 < needed:
        raise ValueError('managementExporter memory budget does not cover declared increment and headroom')
    if value['cpuLimitMillicores'] < p['cpuMillicores']:
        raise ValueError('managementExporter CPU limit is below the declared profile')
    if value['requestConcurrency'] != p['requestConcurrency']:
        raise ValueError('managementExporter request concurrency mismatches declared profile')
    args = value['args']
    if not isinstance(args, list) or not args or any(not isinstance(x, str) or not x for x in args) or args.count('--threads') != 1:
        raise ValueError('managementExporter.args requires explicit normal --threads control')
    pos = args.index('--threads') + 1
    if pos >= len(args) or args[pos] != str(p['threads']):
        raise ValueError('managementExporter normal thread control mismatches profile')
    values = {
        'customConfig.sources.connection_logs.max_line_bytes': str(line),
        'customConfig.sinks.timescape_push_api.request.concurrency': str(value['requestConcurrency']),
        'resources.requests.memory': str(value['memoryRequestMiB'])+'Mi',
        'resources.limits.memory': str(value['memoryLimitMiB'])+'Mi',
        'resources.limits.cpu': str(value['cpuLimitMillicores'])+'m',
    }
    return ([{'name': k, 'value': v} for k, v in values.items()] +
            [{'name': f'args[{i}]', 'value': arg, 'forceString': True} for i, arg in enumerate(args)])
