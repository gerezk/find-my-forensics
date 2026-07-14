import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
import contextily as ctx
import matplotlib.patches as patches
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.collections import PatchCollection
from pathlib import Path

# ----- Configuration (Global Constants) -----
CSV_FILE = Path("../manual_logs.csv")
OUTPUT_DIR = Path("../maps")

LAT_COL = "latitude"
LON_COL = "longitude"
TIME_COL = "timestamp_local"

CELL_SIZE = 200          # meters
FPS = 2
INTERVAL = 50            # milliseconds
DECAY = 0.99
CMAP = "YlOrRd"


def compute_spatial_bounds(gdf: gpd.GeoDataFrame, cell_size: int) \
        -> tuple[np.float64, np.float64, np.float64, np.float64]:
    """
    Calculate spatial bounds based on given GeoDataFrame that is aligned to a grid with square cells of given size.
    :param gdf: geopandas GeoDataFrame
    :param cell_size: size of grid cells in meters
    :return: xmin, xmax, ymin, and ymax as floats
    """
    xmin = np.floor(gdf.geometry.x.min() / cell_size) * cell_size
    xmax = np.ceil(gdf.geometry.x.max() / cell_size) * cell_size
    ymin = np.floor(gdf.geometry.y.min() / cell_size) * cell_size
    ymax = np.ceil(gdf.geometry.y.max() / cell_size) * cell_size

    """Example calculation
    xmin = np.floor(123 / 100) * 100
         = np.floor(1.23) * 100
         = 1 * 100
         = 100
    """

    return xmin, xmax, ymin, ymax


def compute_n_grid_cells(gdf: gpd.GeoDataFrame, cell_size: int) -> tuple[int, int]:
    """
    Calculate number of grid cells based on given GeoDataFrame and cell size.
    :param gdf: geopandas GeoDataFrame
    :param cell_size: size of grid cells in meters
    :return: nx, ny as ints
    """
    xmin, xmax, ymin, ymax = compute_spatial_bounds(gdf, cell_size)

    nx = int((xmax - xmin) / cell_size)
    ny = int((ymax - ymin) / cell_size)

    return nx, ny


def process_gdf(gdf: gpd.GeoDataFrame, cell_size: int) -> gpd.GeoDataFrame:
    """
    Convert each point's real-world coordinates into grid indices.
    :param gdf: geopandas GeoDataFrame
    :param cell_size: size of grid cells in meters
    :return: gdf with added gx and gy columns representing grid indices
    """
    xmin, _, ymin, _ = compute_spatial_bounds(gdf, cell_size)
    nx, ny = compute_n_grid_cells(gdf, cell_size)

    gdf_copy = gdf.copy()
    gdf_copy["gx"] = np.clip(((gdf_copy.geometry.x - xmin) / cell_size).astype(int), 0, nx - 1)
    gdf_copy["gy"] = np.clip(((gdf_copy.geometry.y - ymin) / cell_size).astype(int), 0, ny - 1)

    return gdf_copy


def populate_grid(gdf: gpd.GeoDataFrame, cell_size: int) -> np.ndarray:
    """
    Populates grid cells based on given GeoDataFrame and cell size.
    :param gdf: geopandas GeoDataFrame
    :param cell_size: size of grid cells in meters
    :return: 2D numpy array marking number of location reports per grid cell
    """
    nx, ny = compute_n_grid_cells(gdf, cell_size)
    procesed_gdf = process_gdf(gdf, cell_size)

    grid = np.zeros((ny, nx), dtype=float)
    for gx, gy in zip(procesed_gdf.gx, procesed_gdf.gy):
        grid[gy, gx] += 1

    return grid


def create_static_map(grid: np.ndarray, bounds: tuple[float, float, float, float],
                      cell_size: int, output_path: Path):
    """
    Generates and saves a static heatmap with grid cell borders.
    :param grid: populated 2D numpy array marking number of location reports per grid cell
    :param bounds: spatial bounds
    :param cell_size: size of grid cells in meters
    :param output_path: output path
    :return: None
    """
    xmin, xmax, ymin, ymax = bounds
    fig, ax = plt.subplots(figsize=(10, 10))

    grid_display = grid.copy()
    grid_display[grid_display == 0] = np.nan
    max_pings = np.nanmax(grid_display) if not np.isnan(grid_display).all() else 5

    image = ax.imshow(
        grid_display, extent=bounds, origin="lower", alpha=0.75,
        cmap=CMAP, vmin=1, vmax=max_pings, zorder=10
    )

    # Process all cell borders efficiently
    active_cells = np.argwhere(grid > 0)
    patch_list = [
        patches.Rectangle(
            (xmin + gx * cell_size, ymin + gy * cell_size),
            cell_size, cell_size, edgecolor="black", facecolor="none"
        )
        for gy, gx in active_cells
    ]
    ax.add_collection(PatchCollection(patch_list, match_original=True, zorder=11))

    ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik, reset_extent=False, zorder=0)

    cbar = fig.colorbar(image, ax=ax, shrink=0.7, pad=0.03)
    cbar.set_label(f"Number of Pings per {cell_size}m Cell", fontsize=12, weight='bold')

    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def animate(gdf: gpd.GeoDataFrame, output_path: Path, cell_size: int, decay=False):
    """
    Animates location data dynamically over an interactive background layer.
    :param gdf: geopandas GeoDataFrame
    :param output_path: path to output file
    :param cell_size: size of grid cells in meters
    :param decay: boolean to set if the number of pings per cell should be decayed per timestep
    :return: None
    """
    bounds = compute_spatial_bounds(gdf, cell_size)
    nx, ny = compute_n_grid_cells(gdf, cell_size)
    gdf_local = process_gdf(gdf, cell_size)
    xmin, xmax, ymin, ymax = bounds

    grid = np.zeros((ny, nx), dtype=float)
    fig, ax = plt.subplots(figsize=(10, 10), dpi=100, layout="tight")
    initial_display = np.full((ny, nx), np.nan)

    image = ax.imshow(
        initial_display, extent=bounds, origin="lower", cmap=CMAP,
        alpha=0.75, vmin=1, vmax=5, zorder=10
    )

    ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik, reset_extent=False, zorder=0)
    ax.set_axis_off()

    highlight_box = patches.Rectangle(
        (0, 0), cell_size, cell_size, linewidth=1, edgecolor="black", facecolor="none", zorder=12, visible=False
    )
    ax.add_patch(highlight_box)

    cbar = fig.colorbar(image, ax=ax, shrink=0.7, pad=0.03)
    cbar.set_label(f"Number of Pings per {cell_size}m Cell", fontsize=12, weight='bold')

    time_text = ax.text(
        0.02, 0.98, "", transform=ax.transAxes, color="black", fontsize=12, va="top",
        bbox=dict(facecolor="white", alpha=0.8, edgecolor="none"), zorder=11
    )

    def update(frame):
        nonlocal grid
        if decay:
            grid *= DECAY
            grid[grid < 0.05] = 0

        gx = gdf_local.iloc[frame].gx
        gy = gdf_local.iloc[frame].gy
        grid[gy, gx] += 1

        display_grid = grid.copy()
        display_grid[display_grid == 0] = np.nan
        image.set_data(display_grid)

        current_max = np.nanmax(display_grid) if not np.isnan(display_grid).all() else 5
        image.set_clim(1, max(5, current_max))

        # move highlight box to latest location update
        highlight_box.set_bounds(xmin + gx * cell_size,
                                 ymin + gy * cell_size,
                                 cell_size,
                                 cell_size)
        highlight_box.set_visible(True)

        time_text.set_text(str(gdf_local.iloc[frame][TIME_COL]))

        return image, time_text, highlight_box

    anim = FuncAnimation(fig, update, frames=len(gdf_local), interval=INTERVAL, blit=False)
    anim.save(output_path, fps=FPS, writer="ffmpeg")
    plt.close(fig)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # load data
    df = pd.read_csv(CSV_FILE)
    df[TIME_COL] = pd.to_datetime(df[TIME_COL])
    df = df.sort_values(TIME_COL)

    # # drop consecutive records with identical coordinates
    # mask = df[['latitude', 'longitude']].ne(df[['latitude', 'longitude']].shift()).any(axis=1)
    # df = df[mask]

    # geoprocess coordinates
    gdf_raw = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[LON_COL], df[LAT_COL]),
        crs="EPSG:4326"
    ).to_crs(epsg=3857)

    # calculate grid bounds & mappings
    spatial_bounds = compute_spatial_bounds(gdf_raw, CELL_SIZE)
    gdf_processed = process_gdf(gdf_raw, CELL_SIZE)
    master_grid = populate_grid(gdf_raw, CELL_SIZE)

    # create static map
    create_static_map(
        grid=master_grid,
        bounds=spatial_bounds,
        cell_size=CELL_SIZE,
        output_path=OUTPUT_DIR / "heatmap.png"
    )
    print("Static heatmap updated successfully.")

    # create animated map
    animate(
        gdf=gdf_processed,
        output_path=OUTPUT_DIR / "heatmap.gif",
        cell_size=CELL_SIZE,
        decay=False
    )
    print("Finished.")