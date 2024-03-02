import { JupyterFrontEnd, JupyterFrontEndPlugin, ILayoutRestorer } from '@jupyterlab/application';
import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { MainAreaWidget, ICommandPalette, WidgetTracker } from '@jupyterlab/apputils';
import { ILauncher } from '@jupyterlab/launcher';
import icon from '@datalayer/icons-react/data2/CloudGreyIconJupyterLab';
import { requestAPI } from './handler';
import { ClouderWidget } from './widget';

import '../../style/index.css';

/**
 * The command IDs used by the plugin.
 */
namespace CommandIDs {
  export const create = 'create-clouder-widget';
}

let tracker: WidgetTracker<MainAreaWidget<ClouderWidget>>;

/**
 * Initialization data for the @datalayer/clouder extension.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: '@datalayer/clouder:plugin',
  autoStart: true,
  requires: [ICommandPalette],
  optional: [ISettingRegistry, ILauncher, ILayoutRestorer],
  activate: (
    app: JupyterFrontEnd,
    palette: ICommandPalette,
    settingRegistry?: ISettingRegistry,
    launcher?: ILauncher,
    restorer?: ILayoutRestorer,
  ) => {
    app.version
    const { commands } = app;
    if (!tracker) {
      tracker = new WidgetTracker<MainAreaWidget<ClouderWidget>>({
        namespace: 'clouder',
      });
    }
    const command = CommandIDs.create;
    if (restorer) {
      void restorer.restore(tracker, {
        command,
        name: () => 'clouder',
      });
    }
    commands.addCommand(command, {
      caption: 'Show Clouder',
      label: 'Clouder',
      icon,
      execute: () => {
        const content = new ClouderWidget();
        const widget = new MainAreaWidget<ClouderWidget>({ content });
        widget.title.label = 'Clouder';
        widget.title.icon = icon;
        app.shell.add(widget, 'main');
        tracker.add(widget);
      }
    });
    const category = 'Datalayer';
    palette.addItem({ command, category });
    const settingsUpdated = (settings: ISettingRegistry.ISettings) => {
      const showInLauncher = settings.get('showInLauncher').composite as boolean;
      if (launcher && showInLauncher) {
        launcher.add({
          command,
          category,
          rank: 1.2,
        });
      }
    };
    if (settingRegistry) {
      settingRegistry
        .load(plugin.id)
        .then(settings => {
          console.log(`${plugin.id} settings loaded:`, settings.composite);
          settingsUpdated(settings);
          settings.changed.connect(settingsUpdated);
        })
        .catch(reason => {
          console.error(`Failed to load settings for ${plugin.id}`, reason);
        });
    }
    requestAPI<any>('config')
      .then(data => {
        console.log(data);
      })
      .catch(reason => {
        console.error(
          `Error while accessing the jupyter server extension.\n${reason}`
        );
      }
    );
    console.log('JupyterLab plugin @datalayer/clouder:plugin is activated.');
  }
};

export default plugin;
