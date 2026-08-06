import xbmc
import requests
import json
import datetime
import mariadb

# --- Configuration ---
TMDB_API_KEY = "your_tmdb_api_key_here"
DB_CONFIG = {
    "user": "kodi_user",
    "password": "your_password",
    "host": "192.168.1.100",
    "database": "kodi_scrobbler",
    "port": 3306
}

class ScrobblePlayer(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.session_id = None
        self.current_time = 0
        self.total_time = 0
        self.db = mariadb.connect(**DB_CONFIG)
        self.cursor = self.db.cursor()
        
    def fetch_tmdb_details(self, media_type, tmdb_id):
        """ (Req 4) Connect to TMDB to store details """
        tmdb_type = 'tv' if media_type == 'episode' else 'movie'
        url = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}?api_key={TMDB_API_KEY}"
        try:
            return requests.get(url).json()
        except Exception as e:
            xbmc.log(f"TMDB Fetch Error: {e}", xbmc.LOGERROR)
            return {}

    def onAVStarted(self):
        if not self.isPlayingVideo():
            return
            
        # Allow InfoLabels a brief window to populate
        xbmc.sleep(1000)
        
        media_type = xbmc.getInfoLabel('VideoPlayer.DBTYPE') 
        title = xbmc.getInfoLabel('VideoPlayer.Title')
        tmdb_id = xbmc.getInfoLabel('VideoPlayer.IMDBNumber') # Frequently houses TMDB/IMDB IDs
        
        if not tmdb_id:
            return 
            
        # 1. Fetch TMDB Data
        tmdb_data = self.fetch_tmdb_details(media_type, tmdb_id)
        
        # 2. Check MariaDB for existing incomplete progress
        self.cursor.execute("""
            SELECT progress_sec 
            FROM scrobbles 
            WHERE tmdb_id = ? AND is_finished = 0 
            ORDER BY start_time DESC LIMIT 1
        """, (tmdb_id,))
        
        result = self.cursor.fetchone()
        if result and result[0] > 0:
            # (Req 3) Playback from progress
            self.seekTime(result[0])
            
        # 3. Store watched episode/movie history for this specific session
        now = datetime.datetime.now()
        
        self.cursor.execute("""
            INSERT INTO scrobbles (tmdb_id, media_type, title, start_time, tmdb_metadata)
            VALUES (?, ?, ?, ?, ?)
        """, (tmdb_id, media_type, title, now, json.dumps(tmdb_data)))
        self.db.commit()
        
        # Save the ID of the row we just created so we can update it later
        self.session_id = self.cursor.lastrowid
        
        # Start asynchronous time caching loop
        self._track_time()
        
    def _track_time(self):
        """ 
        Kodi clears playback time instantly upon stopping. 
        We must cache the current time continually while playing.
        """
        while self.isPlaying():
            try:
                self.current_time = self.getTime()
                self.total_time = self.getTotalTime()
            except:
                pass
            xbmc.sleep(2000)
            
    def onPlayBackStopped(self):
        if not self.session_id:
            return
            
        now = datetime.datetime.now()
        
        # (Req 5) Know if it was finished. We consider > 90% as "watched" 
        # to account for credits rolling.
        is_finished = 0
        if self.total_time > 0 and (self.current_time / self.total_time) >= 0.90:
            is_finished = 1
            
        # (Req 2) Remember progress
        self.cursor.execute("""
            UPDATE scrobbles 
            SET progress_sec = ?, total_sec = ?, is_finished = ?, finish_time = ?
            WHERE id = ?
        """, (self.current_time, self.total_time, is_finished, now, self.session_id))
        self.db.commit()
        self.session_id = None

    def onPlayBackEnded(self):
        """ Triggered only when the video naturally plays all the way to the end """
        if not self.session_id:
            return
            
        now = datetime.datetime.now()
        self.cursor.execute("""
            UPDATE scrobbles 
            SET is_finished = 1, finish_time = ?, progress_sec = total_sec
            WHERE id = ?
        """, (now, self.session_id))
        self.db.commit()
        self.session_id = None

if __name__ == '__main__':
    # Initialize Kodi Monitor to keep the script alive
    monitor = xbmc.Monitor()
    player = ScrobblePlayer()
    
    xbmc.log("MariaDB Scrobbler Started", xbmc.LOGINFO)
    
    # Keep background service running until Kodi closes
    while not monitor.abortRequested():
        monitor.waitForAbort(10)
    
    # Cleanup
    if player.db:
        player.db.close()
