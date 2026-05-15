with open('modules/spectrogram_panel.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace initialization to create ax and ax2 once
init_search = """        self.fig = plt.Figure(figsize=(7, 5), facecolor='white')
        self.canvas = FigureCanvasTkAgg(self.fig, master=center_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)"""

init_replace = """        self.fig = plt.Figure(figsize=(7, 5), facecolor='white')
        self.ax = self.fig.add_subplot(111)
        self.ax2 = self.ax.twinx()
        self.canvas = FigureCanvasTkAgg(self.fig, master=center_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)"""

content = content.replace(init_search, init_replace)

# Replace clear_canvas to use ax.clear()
clear_search = """    def clear_canvas(self):
        self.current_item = None
        self.fig.clf()
        self.canvas.draw()"""

clear_replace = """    def clear_canvas(self):
        self.current_item = None
        self.ax.clear()
        self.ax2.clear()
        self.canvas.draw()"""

content = content.replace(clear_search, clear_replace)

# Replace plot_item_spectrogram to not recreate subplots
plot_search = """    def plot_item_spectrogram(self):
        item = self.current_item
        if not item: return
        if not item.get('snd') or item.get('start') is None: return

        self.fig.clf()
        self.ax = self.fig.add_subplot(111)
        self.ax2 = self.ax.twinx()"""

plot_replace = """    def plot_item_spectrogram(self):
        item = self.current_item
        if not item: return
        if not item.get('snd') or item.get('start') is None: return

        self.ax.clear()
        self.ax2.clear()"""

content = content.replace(plot_search, plot_replace)

with open('modules/spectrogram_panel.py', 'w', encoding='utf-8') as f:
    f.write(content)
