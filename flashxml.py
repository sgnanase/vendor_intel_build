#!/usr/bin/env python

import os
import json
import copy
from optparse import OptionParser
from lxml import etree

# main Class to generate xml file from json configuration file
class FlashFileXml:

    def __init__(self, config, platform):
        self.xmlfile = os.path.join(options.directory, config['filename'])
        self.flist = []
        flashtype = config['flashtype']
        self.xml = etree.Element('flashfile')
        self.xml.set('version', '1.0')
        self.add_sub(self.xml, 'id', flashtype)
        self.add_sub(self.xml, 'platform', platform)

    def add_sub(self, parent, name, text):
        sub = etree.SubElement(parent, name)
        sub.text = text

    def add_file(self, filetype, filename, version):
        if filename in self.flist:
            return
        cg = etree.SubElement(self.xml, 'code_group')
        cg.set('name', filetype)
        fl = etree.SubElement(cg, 'file')
        fl.set('TYPE', filetype)
        self.add_sub(fl, 'name', filename)
        self.add_sub(fl, 'version', version)

        self.flist.append(filename)

    def add_command(self, command, description, (timeout, retry, mandatory)):
        mandatory = {True: "1", False: "0"}[mandatory]
        cmd = etree.SubElement(self.xml, 'command')
        self.add_sub(cmd, 'string', command)
        self.add_sub(cmd, 'timeout', str(timeout))
        self.add_sub(cmd, 'retry', str(retry))
        self.add_sub(cmd, 'description', description)
        self.add_sub(cmd, 'mandatory', mandatory)

    def parse_command(self, commands):
        for cmd in commands:
            if 'target' in cmd:
                fname = os.path.basename(t2f[cmd['target']])
                shortname = fname.split('.')[0]
                self.add_file(shortname, fname, 'unspecified')
                cmd['pftname'] = '$' + shortname.lower() + '_file'

        for cmd in commands:
            params = (cmd.get('timeout', 60000), cmd.get('retry', 2), cmd.get('mandatory', True))
            if cmd['type'] == 'fastboot':
                desc = cmd.get('desc', cmd['args'])
                command = 'fastboot ' + cmd['args']
                if 'pftname' in cmd:
                    command += ' ' + cmd['pftname']
            elif cmd['type'] == 'waitForDevice' or cmd['type'] == 'sleep':
                desc = cmd.get('desc', 'Sleep for ' + str(params[0] / 1000) + ' seconds')
                command = 'sleep'
            else:
                continue
            self.add_command(command, desc, params)

    def finish(self):
        print 'writing ', self.xmlfile

        tree = etree.ElementTree(self.xml)
        tree.write(self.xmlfile, xml_declaration=True, encoding="utf-8", pretty_print=True)

# main Class to generate json file from json configuration file
class FlashFileJson:

    def __init__(self, filename, config):
        self.jsonfile = os.path.join(options.directory, filename)
        self.flist = []

        self.configurations = config
        out_cfg = copy.deepcopy(config)
        for cfg_name, cfg in out_cfg.items():
            cfg.pop('commands')
            cfg.pop('subgroup')
            cfg['name'] = cfg_name

        self.flash = {'version': '2.0', 'osplatform': 'android',
                     'parameters': {}, 'configurations': out_cfg, 'commands': []}

    def add_file(self, shortname, filename):
        if filename in self.flist:
            return
        self.flist.append(filename)
        new = {'type': 'file', 'name': shortname, 'value': filename, 'description': filename}
        self.flash['parameters'][shortname] = new

    def add_command(self, new, restrict):

        new['restrict'] = []
        for cfg_name, cfg in self.configurations.items():
            if cfg['commands'] != self.cmd_grp:
                continue
            if restrict and not cfg['subgroup'] in restrict:
                continue
            new['restrict'].append(cfg_name)
        if len(new['restrict']):
            self.flash['commands'].append(new)

    def parse_command(self, commands):
        for cmd in commands:
            if 'target' in cmd:
                fname = os.path.basename(t2f[cmd['target']])
                shortname = fname.split('.')[0].lower()
                self.add_file(shortname, fname)
                cmd['pftname'] = '${' + shortname + '}'

        for cmd in commands:
            new = {}
            new['timeout'] = cmd.get('timeout', 60000)
            new['retry'] = cmd.get('retry', 2)
            new['mandatory'] = cmd.get('mandatory', True)

            if 'variant' in cmd:
                if not variant in cmd['variant']:
                    continue

            if cmd['type'] == 'fastboot':
                new['description'] = cmd.get('desc', cmd['args'])
                new['tool'] = 'fastboot'
                new['args'] = cmd['args']
                if 'pftname' in cmd:
                    new['args'] += ' ' + cmd['pftname']
            elif cmd['type'] == 'waitForDevice':
                new['state'] = cmd.get('state','pos')
                new['description'] = cmd.get('desc', 'Wait for device to enumerate in ' + new['state'])
                new['tool'] = 'waitForDevice'
            elif cmd['type'] == 'sleep':
                new['description'] = cmd.get('desc', 'Wait for ' + str(new['timeout']/1000) + ' seconds')
                new['tool'] = 'sleep'
                new['duration'] = new['timeout']
            else:
                continue
            self.add_command(new, cmd.get('restrict', None))

    def parse_command_grp(self, cmd_groups):
        for grp in cmd_groups:
            self.cmd_grp = grp
            commands = [cmd for cmd in cmd_groups[grp] if not 'target' in cmd or cmd['target'] in t2f]
            self.parse_command(commands)

    def finish(self):
        print 'writing ', self.jsonfile
        self.json = {'flash': self.flash}
        with open(self.jsonfile, "w") as f:
            json.dump(self.json, f, indent=4, sort_keys=True)

def parse_config(conf):
    for c in conf['config']:
        # Special case for json, because it can have multiple configurations
        if c['filename'][-5:] == '.json':
            f = FlashFileJson(c['filename'], conf['configurations'])
            f.parse_command_grp(conf['commands'])
            f.finish()
            continue

        if c['filename'][-4:] == '.xml':
            f = FlashFileXml(c, options.platform)
        elif c['filename'][-4:] == '.cmd':
            f = FlashFileCmd(c)

        commands = conf['commands'][c['commands']]
        commands = [cmd for cmd in commands if not 'target' in cmd or cmd['target'] in t2f]
        commands = [cmd for cmd in commands if not 'variant' in cmd or variant in cmd['variant']]
        commands = [cmd for cmd in commands if not 'restrict' in cmd or c['subgroup'] in cmd['restrict']]

        f.parse_command(commands)
        f.finish()

# dictionnary to translate Makefile "target" name to filename
def init_t2f_dict():
    d = {}
    for l in options.t2f.split():
        target, fname = l.split(':')
        if fname == '':
            print "warning: skip missing target %s" % target
            continue
        d[target] = fname
    return d

def get_env(key, default=None):
    if key in os.environ:
        return os.environ[key]
    return default

def main():
    global options
    global t2f
    global variant

    usage = "usage: %prog [options] flash.xml"
    description = "Tools to generate flash.xml"
    parser = OptionParser(usage, description=description)
    parser.add_option("-p", "--platform", dest="platform", default='default', help="platform refproductname")
    parser.add_option("-d", "--dir", dest="directory", default='.', help="directory to write generated files")
    parser.add_option("-t", "--target2file", dest="t2f", default=None, help="dictionary to translate makefile target to filename")
    (options, args) = parser.parse_args()

    if len(args) != 1:
        parser.print_help()
        return

    with open(args[0], 'rb') as f:
        conf = json.loads(f.read())

    t2f = init_t2f_dict()
    variant = get_env('TARGET_BUILD_VARIANT', 'eng')

    parse_config(conf)

if __name__ == '__main__':
    main()
