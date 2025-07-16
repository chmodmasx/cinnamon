#!/usr/bin/python3

import signal
import os
import sys
from setproctitle import setproctitle

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gio, GLib, Gst, GstVideo

VIDEO_WALLPAPER_DBUS_NAME = "org.Cinnamon.VideoWallpaper"
VIDEO_WALLPAPER_DBUS_PATH = "/org/Cinnamon/VideoWallpaper"

# D-Bus interface XML definition
DBUS_INTERFACE_XML = '''
<node>
    <interface name="org.Cinnamon.VideoWallpaper">
        <method name="play" />
        <method name="pause" />
        <method name="stop" />
        <method name="setVideo">
            <arg type="s" name="uri" direction="in" />
        </method>
        <method name="setVolume">
            <arg type="d" name="volume" direction="in" />
        </method>
        <method name="setLoop">
            <arg type="b" name="loop" direction="in" />
        </method>
    </interface>
</node>
'''

class CinnamonVideoWallpaperApplication(Gio.Application):
    def __init__(self):
        super().__init__(application_id="org.cinnamon.VideoWallpaper")
        
        # Initialize GStreamer
        Gst.init(None)
        
        # Settings
        self.background_settings = Gio.Settings(schema="org.cinnamon.desktop.background")
        
        # GStreamer pipeline
        self.pipeline = None
        self.video_sink = None
        self.audio_sink = None
        
        # State
        self.current_video = ""
        self.is_playing = False
        self.should_loop = True
        self.volume = 0.0
        
        # D-Bus
        self.dbus_connection = None
        self.dbus_registration_id = None

    def do_activate(self):
        self.hold()
        
        # Load initial settings
        self.load_settings()
        
        # Connect to settings changes
        self.background_settings.connect("changed::video-wallpaper-enabled", self.on_video_enabled_changed)
        self.background_settings.connect("changed::video-wallpaper-uri", self.on_video_uri_changed)
        self.background_settings.connect("changed::video-wallpaper-loop", self.on_video_loop_changed)
        self.background_settings.connect("changed::video-wallpaper-volume", self.on_video_volume_changed)
        
        # Setup D-Bus
        self.setup_dbus()
        
        # Start if enabled
        if self.background_settings.get_boolean("video-wallpaper-enabled"):
            self.start_video_wallpaper()

    def load_settings(self):
        """Load current settings from GSettings"""
        self.current_video = self.background_settings.get_string("video-wallpaper-uri")
        self.should_loop = self.background_settings.get_boolean("video-wallpaper-loop")
        self.volume = self.background_settings.get_double("video-wallpaper-volume")

    def setup_dbus(self):
        """Setup D-Bus interface"""
        try:
            self.dbus_connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            introspection_data = Gio.DBusNodeInfo.new_for_xml(DBUS_INTERFACE_XML)
            
            self.dbus_registration_id = self.dbus_connection.register_object(
                VIDEO_WALLPAPER_DBUS_PATH,
                introspection_data.interfaces[0],
                self.handle_dbus_method_call,
                None,
                None
            )
            
            Gio.bus_own_name_on_connection(
                self.dbus_connection,
                VIDEO_WALLPAPER_DBUS_NAME,
                Gio.BusNameOwnerFlags.NONE,
                None,
                None
            )
            
        except Exception as e:
            print(f"Error setting up D-Bus: {e}")

    def handle_dbus_method_call(self, connection, sender, object_path, interface_name, method_name, parameters, invocation):
        """Handle D-Bus method calls"""
        try:
            if method_name == "play":
                self.play()
                invocation.return_value(None)
            elif method_name == "pause":
                self.pause()
                invocation.return_value(None)
            elif method_name == "stop":
                self.stop()
                invocation.return_value(None)
            elif method_name == "setVideo":
                uri = parameters.unpack()[0]
                self.set_video(uri)
                invocation.return_value(None)
            elif method_name == "setVolume":
                volume = parameters.unpack()[0]
                self.set_volume(volume)
                invocation.return_value(None)
            elif method_name == "setLoop":
                loop = parameters.unpack()[0]
                self.set_loop(loop)
                invocation.return_value(None)
            else:
                invocation.return_error_literal(Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, f"Unknown method: {method_name}")
        except Exception as e:
            invocation.return_error_literal(Gio.dbus_error_quark(), Gio.DBusError.FAILED, str(e))

    def create_pipeline(self):
        """Create GStreamer pipeline for video wallpaper"""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None

        if not self.current_video:
            return False

        try:
            # Create pipeline
            self.pipeline = Gst.Pipeline.new("video-wallpaper")
            
            # Create elements
            source = Gst.ElementFactory.make("filesrc", "source")
            demuxer = Gst.ElementFactory.make("decodebin", "demuxer")
            video_convert = Gst.ElementFactory.make("videoconvert", "video-convert")
            video_scale = Gst.ElementFactory.make("videoscale", "video-scale")
            
            # Video sink - render to background
            self.video_sink = Gst.ElementFactory.make("xvimagesink", "video-sink")
            if not self.video_sink:
                self.video_sink = Gst.ElementFactory.make("ximagesink", "video-sink")
            
            # Audio sink
            self.audio_sink = Gst.ElementFactory.make("pulsesink", "audio-sink")
            if not self.audio_sink:
                self.audio_sink = Gst.ElementFactory.make("autoaudiosink", "audio-sink")
            
            # Check if all elements were created
            elements = [source, demuxer, video_convert, video_scale, self.video_sink, self.audio_sink]
            if not all(elements):
                print("Error: Could not create all GStreamer elements")
                return False
            
            # Set properties
            source.set_property("location", self.current_video.replace("file://", ""))
            self.video_sink.set_property("force-aspect-ratio", False)
            self.audio_sink.set_property("volume", self.volume)
            
            # Add elements to pipeline
            for element in elements:
                self.pipeline.add(element)
            
            # Link static elements
            source.link(demuxer)
            video_convert.link(video_scale)
            video_scale.link(self.video_sink)
            
            # Connect demuxer signals for dynamic linking
            demuxer.connect("pad-added", self.on_demuxer_pad_added)
            
            # Set up bus
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_gst_message)
            
            return True
            
        except Exception as e:
            print(f"Error creating pipeline: {e}")
            return False

    def on_demuxer_pad_added(self, demuxer, pad):
        """Handle dynamic pad linking from demuxer"""
        caps = pad.get_current_caps()
        if not caps:
            return
            
        structure = caps.get_structure(0)
        name = structure.get_name()
        
        if name.startswith("video/"):
            # Link video pad
            video_convert = self.pipeline.get_by_name("video-convert")
            sink_pad = video_convert.get_static_pad("sink")
            if not sink_pad.is_linked():
                pad.link(sink_pad)
        elif name.startswith("audio/"):
            # Link audio pad
            sink_pad = self.audio_sink.get_static_pad("sink")
            if not sink_pad.is_linked():
                pad.link(sink_pad)

    def on_gst_message(self, bus, message):
        """Handle GStreamer messages"""
        if message.type == Gst.MessageType.EOS:
            if self.should_loop:
                self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            else:
                self.stop()
        elif message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"GStreamer error: {err}, {debug}")
            self.stop()

    def start_video_wallpaper(self):
        """Start video wallpaper playback"""
        if not self.current_video or not os.path.exists(self.current_video.replace("file://", "")):
            return
            
        if self.create_pipeline():
            self.play()

    def play(self):
        """Play video"""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.PLAYING)
            self.is_playing = True

    def pause(self):
        """Pause video"""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.PAUSED)
            self.is_playing = False

    def stop(self):
        """Stop video"""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.is_playing = False

    def set_video(self, uri):
        """Set video file"""
        self.current_video = uri
        if self.background_settings.get_boolean("video-wallpaper-enabled"):
            self.start_video_wallpaper()

    def set_volume(self, volume):
        """Set audio volume"""
        self.volume = max(0.0, min(1.0, volume))
        if self.audio_sink:
            self.audio_sink.set_property("volume", self.volume)

    def set_loop(self, loop):
        """Set loop mode"""
        self.should_loop = loop

    # Settings change handlers
    def on_video_enabled_changed(self, settings, key):
        """Handle video wallpaper enabled/disabled"""
        if settings.get_boolean(key):
            self.start_video_wallpaper()
        else:
            self.stop()

    def on_video_uri_changed(self, settings, key):
        """Handle video URI change"""
        self.current_video = settings.get_string(key)
        if settings.get_boolean("video-wallpaper-enabled"):
            self.start_video_wallpaper()

    def on_video_loop_changed(self, settings, key):
        """Handle loop setting change"""
        self.should_loop = settings.get_boolean(key)

    def on_video_volume_changed(self, settings, key):
        """Handle volume change"""
        self.volume = settings.get_double(key)
        if self.audio_sink:
            self.audio_sink.set_property("volume", self.volume)

def signal_handler(sig, frame):
    """Handle shutdown signals"""
    print("Shutting down video wallpaper service...")
    sys.exit(0)

if __name__ == "__main__":
    setproctitle("cinnamon-video-wallpaper")
    
    # Handle signals
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    app = CinnamonVideoWallpaperApplication()
    try:
        app.run()
    except KeyboardInterrupt:
        pass
