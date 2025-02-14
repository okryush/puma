#!/usr/bin/env python3
import copy
import glob
import os
from collections import deque
from pathlib import Path

import click
import numpy as np
import open3d as o3d

from puma.mesh import create_mesh_from_map
from puma.preprocessing import preprocess
from puma.registration import register_scan_to_mesh, run_icp
from puma.utils import (
    get_progress_bar,
    load_config_from_yaml,
    print_progress,
    save_config_yaml,
    # save_poses,
    load_poses,
    vel2cam,
)


@click.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True),
    default="config/puma.yml",
    help="Path to the config file",
)
@click.option(
    "--dataset",
    "-d",
    type=click.Path(exists=True),
    default=os.environ["HOME"] + "/data/kitti-odometry/ply/",
    help="Location of the KITTI-like dataset",
)
@click.option(
    "--n_scans",
    "-n",
    type=int,
    default=-1,
    required=False,
    help="Number of scans to integrate",
)
@click.option(
    "--sequence",
    "-s",
    type=str,
    default=None,
    required=False,
    help="Sequence number",
)
@click.option(
    "--odometry_only",
    is_flag=True,
    default=False,
    help="Run odometry only pipeline",
)
def main(config, dataset, n_scans, sequence, odometry_only):
    """This script to run the full puma pipeline as described in the paper. It
    assumes you have the data in the kitti-like format and all the scans where
    already pre-converted to '.ply', for example:

    \b
    kitti/ply
    ├── poses
    │   └── 00.txt
    └── sequences
        └── 00
            ├── calib.txt
            ├── poses.txt
            ├── times.txt
            └── velodyne
                ├── 000000.ply
                ├── 000001.ply
                └── ...

    How to run it and check a quick example:

    \b
    $ ./slam/puma_pipeline.py -d ./data/ -s 00 -n 40
    """
    config = load_config_from_yaml(config)
    if config.debug:
        o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Debug)
    dataset = os.path.join(dataset, "")
    os.makedirs(config.out_dir, exist_ok=True)

    save_plys = False

    if save_plys == True:
        out_w_ply_dir = config.out_dir + 'w_ply/'
        out_ply_raw_dir = config.out_dir + 'ply_raw/'
        out_w_ply_raw_dir = config.out_dir + 'w_ply_raw/'
        os.makedirs(out_w_ply_dir, exist_ok=False)
        os.makedirs(out_ply_raw_dir, exist_ok=False)
        os.makedirs(out_w_ply_raw_dir, exist_ok=False)

    map_name = Path(dataset).name
    if sequence:
        map_name += "_" + sequence
    map_name += "_depth_" + str(config.depth)
    map_name += "_cropped" if config.min_density else ""
    map_name += "_" + config.method
    map_name += "_" + config.strategy

    # Save config
    config_file = map_name + ".yml"
    config_file = os.path.join(config.out_dir, config_file)
    save_config_yaml(config_file, dict(config))

    poses_file = Path(dataset).parents[0].joinpath("poses.txt")
    poses = load_poses(poses_file)
    print("Loaded poses from", poses_file)

    if sequence:
        scans = os.path.join(dataset, "sequences", sequence, "velodyne", "")
    else:
        scans = os.path.join(dataset)
    scan_names = sorted(glob.glob(scans + "*.ply"))

    # Use the whole sequence if -1 is specified
    n_scans = len(scan_names) if n_scans == -1 else n_scans

    # Create data containers to store the map
    mesh = o3d.geometry.TriangleMesh()

    # Create a circular buffer, the same way we do in the C++ implementation
    local_map = deque(maxlen=config.acc_frame_count)

    # Mapping facilities
    global_mesh = o3d.geometry.TriangleMesh()
    mapping_enabled = not odometry_only

    # Start the Odometry and Mapping pipeline
    scan_count = 0
    map_count = 0
    pbar = get_progress_bar(1, n_scans)
    for idx in pbar:
        str_size = print_progress(pbar, idx, n_scans)
        raw_scan = o3d.io.read_point_cloud(scan_names[idx])
        scan = preprocess(raw_scan, config)
        scan.transform(poses[idx])
        local_map.append(scan)

        if save_plys == True:
            stem = os.path.splitext(scan_names[idx].split("/")[-1])[0]
            o3d.io.write_point_cloud(out_w_ply_dir + stem + ".ply", scan)
            o3d.io.write_point_cloud(out_ply_raw_dir + stem + ".ply", raw_scan)
            raw_scan.transform(poses[idx])
            o3d.io.write_point_cloud(out_w_ply_raw_dir + stem + ".ply", raw_scan)

        scan_count += 1
        if scan_count >= config.acc_frame_count or idx == n_scans - 1:
            scan_count = 0
            msg = "[scan #{}] Running PSR over local_map".format(idx)
            pbar.set_description(msg.rjust(str_size))
            mesh, _ = create_mesh_from_map(
                local_map, config.depth, config.n_threads, config.min_density
            )

            global_mesh += mesh
            global_mesh = global_mesh.remove_duplicated_triangles()
            global_mesh = global_mesh.remove_duplicated_vertices()

            map_count += 1
            if map_count >= config.acc_map_count:
                map_count = 0
                mesh_map_file = os.path.join(config.out_dir, map_name + "_iterm.ply")
                print("Saving Map to", mesh_map_file)
                o3d.io.write_triangle_mesh(mesh_map_file, global_mesh)

    if mapping_enabled:
        # Save map to file
        mesh_map_file = os.path.join(config.out_dir, map_name + ".ply")
        print("Saving Map to", mesh_map_file)
        o3d.io.write_triangle_mesh(mesh_map_file, global_mesh)


if __name__ == "__main__":
    main()
