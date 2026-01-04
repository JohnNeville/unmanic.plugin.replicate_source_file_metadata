#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    plugins.__init__.py

    Written by:               Josh.5 <jsunnex@gmail.com>
    Date:                     02 May 2022, (5:19 PM)

    Copyright:
        Copyright (C) 2021 Josh Sunnex

        This program is free software: you can redistribute it and/or modify it under the terms of the GNU General
        Public License as published by the Free Software Foundation, version 3.

        This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
        implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
        for more details.

        You should have received a copy of the GNU General Public License along with this program.
        If not, see <https://www.gnu.org/licenses/>.

"""
import json
import logging
import os
import shutil
import subprocess
import tempfile

from unmanic.libs.unplugins.settings import PluginSettings

# Configure plugin logger
logger = logging.getLogger("Unmanic.Plugin.replicate_source_file_metadata")


class Settings(PluginSettings):
    settings = {
        "replicate_creation_time": True,
    }

    def __init__(self, *args, **kwargs):
        super(Settings, self).__init__(*args, **kwargs)
        self.form_settings = {
            "replicate_creation_time": {
                "label": "Replicate the source file's 'creation_time' metadata to the destination file.",
                "field_type": "boolean",
                "value": True,
            },
        }


def get_file_metadata(path_to_file):
    """Return the file metadata based on plugin config"""
    return_data = {}
    try:
        process = subprocess.Popen(
            [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                path_to_file
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding='utf-8'
        )
        stdout, stderr = process.communicate()
        if stderr:
            logger.error("Failed to read metadata from file '{}' - {}".format(path_to_file, stderr))
            return return_data

        probe_data = json.loads(stdout)
        
        # Get creation_time from format tags
        if 'format' in probe_data and 'tags' in probe_data['format'] and 'creation_time' in probe_data['format']['tags']:
            return_data['creation_time'] = probe_data['format']['tags']['creation_time']
            return return_data

        # If not in format, check streams
        for stream in probe_data.get('streams', []):
            if 'tags' in stream and 'creation_time' in stream['tags']:
                return_data['creation_time'] = stream['tags']['creation_time']
                return return_data
                
    except Exception as e:
        logger.error("Exception while reading metadata from file '{}' - {}".format(path_to_file, e))

    return return_data


def on_postprocessor_file_movement(data):
    """
    Runner function - configures additional postprocessor file movements during the postprocessor stage of a task.

    The 'data' object argument includes:
        library_id              - Integer, the library that the current task is associated with.
        source_data             - Dictionary, data pertaining to the original source file.
        remove_source_file      - Boolean, should Unmanic remove the original source file after all copy operations
                                  are complete. (default: 'True' if file name has changed)
        copy_file               - Boolean, should Unmanic run a copy operation with the returned data variables.
                                  (default: 'False')
        file_in                 - String, the converted cache file to be copied by the postprocessor.
        file_out                - String, the destination file that the file will be copied to.
        run_default_file_copy   - Boolean, should Unmanic run the default post-process file movement. (default: 'True')

    :param data:
    :return:

    """
    # Configure settings object
    settings = Settings(library_id=data.get('library_id'))
    if not settings.get_setting('replicate_creation_time'):
        return

    # Get the original file's absolute path
    original_source_path = data.get('source_data', {}).get('abspath')
    if not original_source_path:
        logger.error("Provided 'source_data' is missing the source file abspath data.")
        return

    # Store some required data in a JSON file for the on_postprocessor_task_results runner.
    cache_directory = os.path.dirname(data.get('file_in'))
    if not os.path.exists(cache_directory):
        os.makedirs(cache_directory)
    
    plugin_data_file = os.path.join(cache_directory, 'replicate_metadata.json')
    with open(plugin_data_file, 'w') as f:
        required_data = get_file_metadata(original_source_path)
        json.dump(required_data, f, indent=4)


def on_postprocessor_task_results(data):
    """
    Runner function - provides a means for additional postprocessor functions based on the task success.

    The 'data' object argument includes:
        final_cache_path                - The path to the final cache file that was then used as the source for all destination files.
        library_id                      - The library that the current task is associated with.
        task_processing_success         - Boolean, did all task processes complete successfully.
        file_move_processes_success     - Boolean, did all postprocessor movement tasks complete successfully.
        destination_files               - List containing all file paths created by postprocessor file movements.
        source_data                     - Dictionary containing data pertaining to the original source file.

    :param data:
    :return:
    
    """
    settings = Settings(library_id=data.get('library_id'))
    if not settings.get_setting('replicate_creation_time'):
        return

    # Read the original file's data
    cache_directory = os.path.dirname(data.get('final_cache_path'))
    plugin_data_file = os.path.join(cache_directory, 'replicate_metadata.json')
    if not os.path.exists(plugin_data_file):
        logger.warning("Plugin data file is missing. Skipping metadata replication.")
        return
    
    with open(plugin_data_file) as infile:
        source_file_data = json.load(infile)

    creation_time = source_file_data.get('creation_time')
    if not creation_time:
        logger.info("No creation_time metadata found in source file. Skipping.")
        return

    for destination_file in data.get('destination_files'):
        if not os.path.exists(destination_file):
            logger.error("Unable to find destination file '{}'".format(destination_file))
            continue

        try:
            # Use a temporary file in the same directory as the destination
            temp_output_file = tempfile.NamedTemporaryFile(
                delete=False,
                suffix='.mp4',
                dir=os.path.dirname(destination_file)
            ).name

            ffmpeg_command = [
                'ffmpeg',
                '-i', destination_file,
                '-c', 'copy',
                '-metadata', 'creation_time={}'.format(creation_time),
                '-metadata:s:v:0', 'creation_time={}'.format(creation_time),
                '-metadata:s:a:0', 'creation_time={}'.format(creation_time),
                '-y',  # Overwrite temp file if it exists
                temp_output_file
            ]

            logger.info("Running ffmpeg to update metadata: {}".format(" ".join(ffmpeg_command)))

            process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                logger.error("Failed to update metadata for '{}'".format(destination_file))
                logger.error(stderr)
                if os.path.exists(temp_output_file):
                    os.remove(temp_output_file)
                continue

            # Safety check: ensure the temp file is not empty
            if os.path.getsize(temp_output_file) == 0:
                logger.error("ffmpeg created an empty file. Aborting to prevent data loss.")
                os.remove(temp_output_file)
                continue

            # Replace the original file with the one with updated metadata
            os.rename(temp_output_file, destination_file)
            logger.info("Successfully updated creation_time for '{}'".format(destination_file))

        except Exception as e:
            logger.error("Exception while updating metadata for file '{}' - {}".format(destination_file, e))
            if 'temp_output_file' in locals() and os.path.exists(temp_output_file):
                try:
                    os.remove(temp_output_file)
                except OSError as ose:
                    logger.error(f"Error removing temporary file {temp_output_file}: {ose}")

    # Clean up the plugin data file
    if os.path.exists(plugin_data_file):
        os.remove(plugin_data_file)

    return
