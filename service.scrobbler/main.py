import xbmc
import xbmcaddon
import requests
import json
import datetime
import mariadb

class ScrobblerMonitor(xbmc.Monitor):
    def __init__(self, player):
        super().__init__()
        self.player = player

    def onSettingsChanged(self):
        """Triggered automatically when the user clicks 'OK' in the Addon Settings GUI."""
        xbmc.log("MariaDB Scrobbler: Settings changed. Reloading connection...", xbmc.LOGINFO)
        self.player.load_settings()

class ScrobblePlayer(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.session_id = None
        self.current_time = 0
        self.total_time = 0
        self.db = None
        self.cursor = None
        self.tmdb_api_key = ""
        
        # Load credentials immediately on startup
        self.load_settings()
        
    def load_settings(self):
        addon = xbmcaddon.Addon()
        self.tmdb_api_key = addon.getSetting('tmdb_api_key')
        
        # Fetch DB parameters, falling back to defaults if empty
        host = addon.getSetting('db_host') or "192.168.1.100"
        port_str = addon.getSetting('db_port')
        port = int(port_str) if port_str.isdigit() else 3306
        user = addon.getSetting('db_user') or "kodi_user"
        password = addon.getSetting('db_pass')
        database = addon.getSetting('db_name') or "kodi_scrobbler"
        
        # If changing settings live, close the old connection first
        if self.db:
            try:
                self.db.close()
            except:
                pass
                
        try:
            self.db = mariadb.connect(
                host=host, port=port, user=user, password=password, database=database
            )
            self.cursor = self.db.cursor()
            xbmc.log("MariaDB Scrobbler: Connected to DB successfully", xbmc.LOGINFO)
        except mariadb.Error as e:
            self.db = None
            xbmc.log(f"MariaDB Scrobbler DB Error: {e}", xbmc.LOGERROR)

    def fetch_tmdb_details(self, media_type, tmdb_id):
        if not self.tmdb_api_key:
            return {}
            
        tmdb_type = 'tv' if media_type == 'episode' else 'movie'
        url = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}?api_key={self.tmdb_api_key}"
        try:
            return requests.get(url).json()
        except Exception as e:
            xbmc.log(f"TMDB Fetch Error: {e}", xbmc.LOGERROR)
            return {}

    def onAVStarted(self):
        # Guard clause: Fail gracefully if DB connection is broken
        if not self.isPlayingVideo() or not self.db:
            return
            
        xbmc.sleep(1000)
        
        media_type = xbmc.getInfoLabel('VideoPlayer.DBTYPE') 
        title = xbmc.getInfoLabel('VideoPlayer.Title')
        tmdb_id = xbmc.getInfoLabel('VideoPlayer.IMDBNumber')
        
        if not tmdb_id:
            return 
            
        tmdb_data = self.fetch_tmdb_details(media_type, tmdb_id)
        
        try:
            self.cursor.execute("""
                SELECT progress_sec 
                FROM scrobbles 
                WHERE tmdb_id = ? AND is_finished = 0 
                ORDER BY start_time DESC LIMIT 1
            """, (tmdb_id,))
            
            result = self.cursor.fetchone()
            if result and result[0] > 0:
                self.seekTime(result[0])
                
            now = datetime.datetime.now()
            
            self.cursor.execute("""
                INSERT INTO scrobbles (tmdb_id, media_type, title, start_time, tmdb_metadata)
                VALUES (?, ?, ?, ?, ?)
            """, (tmdb_id, media_type, title, now, json.dumps(tmdb_data)))
            self.db.commit()
            
            self.session_id = self.cursor.lastrowid
        except mariadb.Error as e:
            xbmc.log(f"MariaDB Scrobbler Insert Error: {e}", xbmc.LOGERROR)
            return
            
        self._track_time()
        
    def _track_time(self):
        while self.isPlaying():
            try:
                self.current_time = self.getTime()
                self.total_time = self.getTotalTime()
            except:
                pass
            xbmc.sleep(2000)
            
    def onPlayBackStopped(self):
        if not self.session_id or not self.db:
            return
            
        now = datetime.datetime.now()
        is_finished = 1 if (self.total_time > 0 and (self.current_time / self.total_time) >= 0.90) else 0
            
        try:
            self.cursor.execute("""
                UPDATE scrobbles 
                SET progress_sec = ?, total_sec = ?, is_finished = ?, finish_time = ?
                WHERE id = ?
            """, (self.current_time, self.total_time, is_finished, now, self.session_id))
            self.db.commit()
        except mariadb.Error as e:
            xbmc.log(f"MariaDB Scrobbler Update Error: {e}", xbmc.LOGERROR)
            
        self.session_id = None

    def onPlayBackEnded(self):
        if not self.session_id or not self.db:
            return
            
        now = datetime.datetime.now()
        try:
            self.cursor.execute("""
                UPDATE scrobbles 
                SET is_finished = 1, finish_time = ?, progress_sec = total_sec
                WHERE id = ?
            """, (now, self.session_id))
            self.db.commit()
        except mariadb.Error as e:
             xbmc.log(f"MariaDB Scrobbler End Error: {e}", xbmc.LOGERROR)
             
        self.session_id = None

if __name__ == '__main__':
    player = ScrobblePlayer()
    
    # Pass the player instance to the Monitor so it can trigger reloads
    monitor = ScrobblerMonitor(player)
    
    xbmc.log("MariaDB Scrobbler Started", xbmc.LOGINFO)
    
    while not monitor.abortRequested():
        monitor.waitForAbort(10)
    
    if player.db:
        player.db.close()
