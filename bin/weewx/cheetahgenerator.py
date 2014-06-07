#
#    Copyright (c) 2009-2014 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
#    $Id$
#
"""Generate files from templates using the Cheetah template engine.

For more information about Cheetah, see http://www.cheetahtemplate.org

Configuration Options

  search_list = a, b, c              # list of classes derived from SearchList
  search_list_additions = d, e, f    # will be appended to search_list. Each should be a list.
  encoding = (html_entities|utf8|strict_ascii)
  template = filename.tmpl           # must end with .tmpl
  stale_age = s                      # age in seconds

The strings YYYY and MM will be replaced if they appear in the filename.

search_list will override the default search_list

search_list_additions will be appended to search_list

Generally it is better to extend by using search_list_additions rather than
search_list, just in case the default search list changes.

Example:

[CheetahGenerator]
    # This is the new way to specify a search list additions:
    search_list_additions = user.special.MyExtension
    # This is the old way and is included for backwards compatibility, 
    # but will eventually be deprecated:
    search_list_extensions = user.forecast.ForecastVariables, user.extstats.ExtStatsVariables
    encoding = html_entities      # html_entities, utf8, strict_ascii
    [[SummaryByMonth]]                              # period
        [[[NOAA_month]]]                            # report
            encoding = strict_ascii
            template = NOAA-YYYY-MM.txt.tmpl
    [[SummaryByYear]]
        [[[NOAA_year]]]]
            encoding = strict_ascii
            template = NOAA-YYYY.txt.tmpl
    [[ToDate]]
        [[[day]]]
            template = index.html.tmpl
        [[[week]]]
            template = week.html.tmpl
    [[wuforecast_details]]                 # period/report
        stale_age = 3600                   # how old before regenerating
        template = wuforecast.html.tmpl
    [[nwsforecast_details]]                # period/report
        stale_age = 10800                  # how old before generating
        template = nwsforecast.html.tmpl

"""

from __future__ import with_statement
import os.path
import syslog
import time

import configobj

import Cheetah.Template
import Cheetah.Filters

import weeutil.weeutil
import weewx.almanac
import weewx.reportengine
import weewx.station
import weewx.units
import weewx.tags
from weeutil.weeutil import to_int

# Default search list:
default_search_list = [
    "weewx.cheetahgenerator.Almanac",
    "weewx.cheetahgenerator.Station",
    "weewx.cheetahgenerator.Stats",
    "weewx.cheetahgenerator.UnitInfo",
    "weewx.cheetahgenerator.Extras"]

def logmsg(lvl, msg):
    syslog.syslog(lvl, 'cheetahgenerator: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)

def logcrt(msg):
    logmsg(syslog.LOG_CRIT, msg)

# =============================================================================
# CheetahGenerator
# =============================================================================

class CheetahGenerator(weewx.reportengine.ReportGenerator):
    """Class for generating files from cheetah templates.
    
    Useful attributes (some inherited from ReportGenerator):

        config_dict:      The weewx configuration dictionary 
        skin_dict:        The dictionary for this skin
        gen_ts:           The generation time
        first_run:        Is this the first time the generator has been run?
        stn_info:         An instance of weewx.station.StationInfo
        formatter:        An instance of weewx.units.Formatter
        converter:        An instance of weewx.units.Converter
        unitInfoHelper:   An instance of weewx.units.UnitInfoHelper
        search_list_exts: A list holding search list extensions, new style
        search_list_objs: A list holding search list extensions, old style
        db_cache:         An instance of weewx.archive.DBCache from which the data should be extracted
    """

    generator_dict = {'SummaryByMonth': weeutil.weeutil.genMonthSpans,
                      'SummaryByYear' : weeutil.weeutil.genYearSpans}

    def run(self):
        """Main entry point for file generation using Cheetah Templates."""

        t1 = time.time()

        self.setup()
        
        # Make a copy of the skin dictionary (we will be modifying it):
        gen_dict = configobj.ConfigObj(self.skin_dict.dict())
        
        # Look for options in [CheetahGenerator], but accept options from
        # [FileGenerator] for backward compatibility.  
        option_section_name = "CheetahGenerator" if gen_dict.has_key('CheetahGenerator') else "FileGenerator"
        
        # The default summary time span is 'None'.
        gen_dict[option_section_name]['summarize_by'] = 'None'

        self.initExtensions(gen_dict[option_section_name])

        # Generate any templates in the given dictionary:
        ngen = self.generate(gen_dict[option_section_name], self.gen_ts)

        self.teardown()

        elapsed_time = time.time() - t1
        loginf("Generated %d files for report %s in %.2f seconds" %
               (ngen, self.skin_dict['REPORT_NAME'], elapsed_time))

    def setup(self):
        self.outputted_dict = {'SummaryByMonth' : [], 'SummaryByYear'  : [] }

        self.formatter = weewx.units.Formatter.fromSkinDict(self.skin_dict)
        self.converter = weewx.units.Converter.fromSkinDict(self.skin_dict)

    def initExtensions(self, gen_dict):
        """Load the search list"""
        # New style search list additions:
        self.search_list_objs = []
        # Old style search list extensions:
        self.search_list_exts = []

        # The option search_list contains the basic search list
        search_list = weeutil.weeutil.option_as_list(gen_dict.get('search_list'))
        if search_list is None:
            search_list = list(default_search_list)

        # The option search_list_additions contains the extensions
        search_list_addns = weeutil.weeutil.option_as_list(gen_dict.get('search_list_additions'))
        if search_list_addns is not None:
            search_list.extend(search_list_addns)

        # Now go through search_list (which is a list of strings holding the names of the extensions):
        for c in search_list:
            x = c.strip()
            if x:
                # Get the class
                class_ = weeutil.weeutil._get_object(x)
                # Then instantiate the class, passing self as the sole argument
                self.search_list_objs.append(class_(self))
                
        # For backwards compatibility, get the search list extensions
        search_list_extensions = weeutil.weeutil.option_as_list(gen_dict.get('search_list_extensions'))
        if search_list_extensions is not None:
            for c in search_list_extensions:
                x = c.strip()
                if x:
                    class_ = weeutil.weeutil._get_object(x)
                    self.search_list_exts.append(class_(self))

    def teardown(self):
        """Delete any extension objects we created to prevent back references
        from slowing garbage collection"""
        while len(self.search_list_objs):
            del self.search_list_objs[-1]
            
    def generate(self, section, gen_ts):
        """Generate one or more reports for the indicated section.  Each section
        in a period is a report.  A report has one or more templates.

        section: A ConfigObj dictionary, holding the templates to be generated. Any
        subsections in the dictionary will be recursively processed as well.
        
        gen_ts: The report will be current to this time.
        """
        
        ngen = 0
        # Go through each subsection, generating from any templates they may contain
        for subsection in section.sections:
            # Sections 'SummaryByMonth' and 'SummaryByYear' imply summarize_by certain time spans
            if not section[subsection].has_key('summarize_by'):
                if subsection == 'SummaryByMonth':
                    section[subsection]['summarize_by'] = 'SummaryByMonth'
                elif subsection == 'SummaryByYear':
                    section[subsection]['summarize_by'] = 'SummaryByYear'
            # Call myself recursively, to generate any templates in this subsection
            ngen += self.generate(section[subsection], gen_ts)

        # We have finished recursively processing any subsections in this
        # section. Time to do the section itself. If there is no option
        # 'template', then there isn't anything to do. Return.
        if not section.has_key('template'):
            return ngen
        
        # Change directory to the skin subdirectory.  We use absolute paths
        # for cheetah, so the directory change is not necessary for generating
        # files.  However, changing to the skin directory provides a known
        # location so that calls to os.getcwd() in any templates will return
        # a predictable result.
        os.chdir(os.path.join(self.config_dict['WEEWX_ROOT'],
                              self.skin_dict['SKIN_ROOT'],
                              self.skin_dict['skin']))

        report_dict = weeutil.weeutil.accumulateLeaves(section)
        
        (template, dest_dir, encoding, default_database) = self._prepGen(report_dict)

        # Get start and stop times        
        default_archive = self.db_cache.get_database(default_database)
        start_ts = default_archive.firstGoodStamp()
        if not start_ts:
            loginf('skipping report %s: cannot find start time' % section)
            return ngen
        stop_ts = gen_ts if gen_ts else default_archive.lastGoodStamp()

        # Get an appropriate generator function
        summarize_by = report_dict['summarize_by']
        if summarize_by in CheetahGenerator.generator_dict:
            _spangen = CheetahGenerator.generator_dict[summarize_by]
        else:
            # Just a single timespan to generate. Use a lambda expression.
            _spangen = lambda start_ts, stop_ts : [weeutil.weeutil.TimeSpan(start_ts, stop_ts)]

        # Use the generator function
        for timespan in _spangen(start_ts, stop_ts):

            # Save YYYY-MM so they can be used within the document
            if summarize_by in CheetahGenerator.generator_dict:
                timespan_start_tt = time.localtime(timespan.start)
                _yr_str = "%4d" % timespan_start_tt[0]
                if summarize_by == 'SummaryByMonth':
                    _mo_str = "%02d" % timespan_start_tt[1]
                    if _mo_str not in self.outputted_dict[summarize_by]:
                        self.outputted_dict[summarize_by].append("%s-%s" % (_yr_str, _mo_str))
                if summarize_by == 'SummaryByYear' and _yr_str not in self.outputted_dict[summarize_by]:
                    self.outputted_dict[summarize_by].append(_yr_str)

            # figure out the filename for this template
            _filename = self._getFileName(template, timespan)
            _fullname = os.path.join(dest_dir, _filename)

            # Skip summary files outside the timespan
            if report_dict['summarize_by'] in CheetahGenerator.generator_dict \
                    and os.path.exists(_fullname) \
                    and not timespan.includesArchiveTime(stop_ts):
                continue

            # skip files that are fresh, only if staleness is defined
            stale = to_int(report_dict.get('stale_age'))
            if stale is not None:
                t_now = time.time()
                try:
                    last_mod = os.path.getmtime(_fullname)
                    if t_now - last_mod < stale:
                        logdbg("skip '%s': last_mod=%s age=%s stale=%s" %
                               (_filename, last_mod, t_now - last_mod, stale))
                        continue
                except os.error:
                    pass

            searchList = self._getSearchList(encoding, timespan,
                                             default_database)
            
            text = Cheetah.Template.Template(file=template,
                                             searchList=searchList,
                                             filter=encoding,
                                             filtersLib=weewx.cheetahgenerator)
            tmpname = _fullname + '.tmp'
            try:
                with open(tmpname, mode='w') as _file:
                    print >> _file, text
                os.rename(tmpname, _fullname)
            except Exception, e:
                logerr("Generate failed with exception '%s'" % type(e))
                logerr("**** Ignoring template %s" % template)
                logerr("**** Reason: %s" % e)
                weeutil.weeutil.log_traceback("****  ")
            else:
                ngen += 1
            finally:
                try:
                    os.unlink(tmpname)
                except OSError:
                    pass

        return ngen

    def _getSearchList(self, encoding, timespan, default_database):
        """Get the complete search list to be used by Cheetah."""

        timespan_start_tt = time.localtime(timespan.start)
        db_factory = weewx.tags.DBFactory(self.db_cache, default_database)
        
        # For backwards compatibility, extract the default archive and stats databases
        statsdb = archivedb = db_factory.get_database()
        
        # Get the basic search list
        searchList = [{'month_name' : time.strftime("%b", timespan_start_tt),
                       'year_name'  : timespan_start_tt[0],
                       'encoding' : encoding},
                      self.outputted_dict]
        
        # Then add the new-style search list additions:
        for obj in self.search_list_objs:
            searchList += obj.add_searchlist(timespan, db_factory)

        # Now add the old-style extensions:
        searchList += [obj.get_extension(timespan, archivedb, statsdb) for obj in self.search_list_exts]
        
        # Finally, add the REALLY old style extensions
        searchList += self.getToDateSearchList(archivedb, statsdb, timespan)

        return searchList

    def getToDateSearchList(self, archivedb, statsdb, timespan):
        """Backwards compatible entry."""
        return []

    def _getFileName(self, template, timespan):
        """Calculate a destination filename given a template filename.
        Replace 'YYYY' with the year, 'MM' with the month.  Strip off any
        trailing .tmpl"""

        _filename = os.path.basename(template).replace('.tmpl', '')

        if _filename.find('YYYY') >= 0 or _filename.find('MM') >= 0:
            # Start by getting the start time as a timetuple.
            timespan_start_tt = time.localtime(timespan.start)
            # Get a string representing the year (e.g., '2009') and month
            _yr_str = "%4d"  % timespan_start_tt[0]
            _mo_str = "%02d" % timespan_start_tt[1]
            # Replace any instances of 'YYYY' with the year string
            _filename = _filename.replace('YYYY', _yr_str)
            # Do the same thing with the month
            _filename = _filename.replace('MM', _mo_str)

        return _filename

    def _prepGen(self, report_dict):
        """Gather the options together for a specific report, then
        retrieve the template file, stats database, archive database,
        the destination directory, and the encoding from those options."""

        template = os.path.join(self.config_dict['WEEWX_ROOT'],
                                self.config_dict['StdReport']['SKIN_ROOT'],
                                report_dict['skin'],
                                report_dict['template'])
        destination_dir = os.path.join(self.config_dict['WEEWX_ROOT'],
                                       report_dict['HTML_ROOT'],
                                       os.path.dirname(report_dict['template']))
        encoding = report_dict.get('encoding', 'html_entities').strip().lower()
        if encoding == 'utf-8':
            encoding = 'utf8'

        default_database = report_dict['database']

        try:
            # Create the directory that is to receive the generated files.  If
            # it already exists an exception will be thrown, so be prepared to
            # catch it.
            os.makedirs(destination_dir)
        except OSError:
            pass

        return (template, destination_dir, encoding, default_database)

# =============================================================================
# Classes used to implement the Search list
# =============================================================================

class SearchList(object):
    """Provide binding between variable name and data"""

    def __init__(self, generator):
        """Create an instance of the search list.

        generator: The generator that is using this search list
        """
        self.generator = generator

    def add_searchlist(self, timespan, db_factory):
        """Derived classes must define this method.  Should return a list
        of objects whose attributes or keys define the extension.
        
        timespan:  An instance of weeutil.weeutil.TimeSpan. This will hold the
                   start and stop times of the domain of valid times.
                   
        db_factory: An instance of class weewx.unidata.UniFactory
        """
        return [self]

    def get_extension(self, timespan, archivedb, statsdb):
        """Derived classes must define this method.  Should return an object
        whose attributes or keys define the extension.
        
        OBSOLETE INTERFACE. Use add_searchlist() instead.
        
        timespan:  An instance of weeutil.weeutil.TimeSpan. This will hold the
                   start and stop times of the domain of valid times.
                   
        archivedb: An instance of class weewx.archive.Archive.
        
        statsdb:   An instance of class weewx.stats.StatsDb
        """
        return self
    
class Almanac(SearchList):
    """Class that implements the '$almanac' tag."""

    def __init__(self, generator):
        SearchList.__init__(self, generator)

        celestial_ts = generator.gen_ts

        # For better accuracy, the almanac requires the current temperature
        # and barometric pressure, so retrieve them from the default archive,
        # using celestial_ts as the time

        # The default values of temperature and pressure
        temperature_C = 15.0
        pressure_mbar = 1010.0

        # See if we can get more accurate values by looking them up in the weather
        # database. The database might not exist, so be prepared for a KeyError exception.
        try:
            archive = self.generator.db_cache.get_database()
        except KeyError:
            pass
        else:
            if not celestial_ts:
                celestial_ts = archive.lastGoodStamp()
    
            if celestial_ts:
                # Look for the record closest in time. Up to one hour off is fine:    
                rec = archive.getRecord(celestial_ts, max_delta=3600)
                if rec is not None:
        
                    # Wrap the record in a ValueTupleDict. This makes it easy to do
                    # unit conversions.
                    rec_vtd = weewx.units.ValueTupleDict(rec)
                    
                    if rec_vtd.has_key('outTemp'):
                        temperature_C = weewx.units.convert(rec_vtd['outTemp'], 'degree_C')[0]
        
                    if rec_vtd.has_key('barometer'):
                        pressure_mbar = weewx.units.convert(rec_vtd['barometer'], 'mbar')[0]
        
        self.moonphases = generator.skin_dict.get('Almanac', {}).get('moon_phases', weeutil.Moon.moon_phases)

        altitude_vt = weewx.units.convert(generator.stn_info.altitude_vt, "meter")

        self.almanac = weewx.almanac.Almanac(celestial_ts,
                                             generator.stn_info.latitude_f,
                                             generator.stn_info.longitude_f,
                                             altitude=altitude_vt[0],
                                             temperature=temperature_C,
                                             pressure=pressure_mbar,
                                             moon_phases=self.moonphases,
                                             formatter=generator.formatter)

class Station(SearchList):
    """Class that implements the $station tag."""

    def __init__(self, generator):
        SearchList.__init__(self, generator)
        self.station = weewx.station.Station(generator.stn_info,
                                             generator.formatter,
                                             generator.converter,
                                             generator.skin_dict)
        
class Stats(SearchList):
    """Class that implements the time-based statistical tags, such
    as $day.outTemp.max"""

    # Default base temperature and unit type for heating and cooling degree days
    # as a value tuple
    default_heatbase = (65.0, "degree_F", "group_temperature")
    default_coolbase = (65.0, "degree_F", "group_temperature")

    def add_searchlist(self, timespan, db_factory):
        units_dict = self.generator.skin_dict.get('Units', {})
        dd_dict = units_dict.get('DegreeDays', {})
        heatbase = dd_dict.get('heating_base', None)
        coolbase = dd_dict.get('cooling_base', None)
        heatbase_t = (float(heatbase[0]), heatbase[1], "group_temperature") if heatbase else Stats.default_heatbase
        coolbase_t = (float(coolbase[0]), coolbase[1], "group_temperature") if coolbase else Stats.default_coolbase

        try:
            time_delta = int(self.generator.skin_dict['Units']['Trend']['time_delta'])
            time_grace = int(self.generator.skin_dict['Units']['Trend'].get('time_grace', 300))
        except KeyError:
            time_delta = 10800  # 3 hours
            time_grace = 300    # 5 minutes

        stats = weewx.tags.FactoryBinder(db_factory,
                                         timespan.stop,
                                         formatter=self.generator.formatter,
                                         converter=self.generator.converter,
                                         rain_year_start=self.generator.stn_info.rain_year_start,
                                         heatbase=heatbase_t,
                                         coolbase=coolbase_t,
                                         week_start=self.generator.stn_info.week_start,
                                         time_delta=time_delta,
                                         time_grace=time_grace)

        return [stats]

class UnitInfo(SearchList):
    """Class that implements the $unit tag."""

    def __init__(self, generator):
        SearchList.__init__(self, generator)
        self.unit = weewx.units.UnitInfoHelper(generator.formatter,
                                               generator.converter)

class Extras(SearchList):
    """Class for exposing the [Extras] section in the skin config dictionary
    as tag $Extras."""

    def __init__(self, generator):
        SearchList.__init__(self, generator)
        # If the user has supplied an '[Extras]' section in the skin
        # dictionary, include it in the search list. Otherwise, just include
        # an empty dictionary.
        self.Extras = generator.skin_dict['Extras'] if generator.skin_dict.has_key('Extras') else {}
    
# =============================================================================
# Filters used for encoding
# =============================================================================

class html_entities(Cheetah.Filters.Filter):

    def filter(self, val, **dummy_kw): #@ReservedAssignment
        """Filter incoming strings so they use HTML entity characters"""
        if isinstance(val, unicode):
            filtered = val.encode('ascii', 'xmlcharrefreplace')
        elif val is None:
            filtered = ''
        elif isinstance(val, str):
            filtered = val.decode('utf-8').encode('ascii', 'xmlcharrefreplace')
        else:
            filtered = self.filter(str(val))
        return filtered

class strict_ascii(Cheetah.Filters.Filter):

    def filter(self, val, **dummy_kw): #@ReservedAssignment
        """Filter incoming strings to strip out any non-ascii characters"""
        if isinstance(val, unicode):
            filtered = val.encode('ascii', 'ignore')
        elif val is None:
            filtered = ''
        elif isinstance(val, str):
            filtered = val.decode('utf-8').encode('ascii', 'ignore')
        else:
            filtered = self.filter(str(val))
        return filtered
    
class utf8(Cheetah.Filters.Filter):

    def filter(self, val, **dummy_kw): #@ReservedAssignment
        """Filter incoming strings, converting to UTF-8"""
        if isinstance(val, unicode):
            filtered = val.encode('utf8')
        elif val is None:
            filtered = ''
        else:
            filtered = str(val)
        return filtered
