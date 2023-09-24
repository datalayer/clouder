import { useState, useEffect } from 'react';
import { Box, Text } from '@primer/react';
import { Table, DataTable } from '@primer/react/drafts';
import { requestAPI } from '../../jupyterlab/handler';

type OvhProject = {
  id: number,
  projectId: string;
}

const KeysTab = () => {
  const [projects, setProjects] = useState(new Array<OvhProject>());
  useEffect(() => {
    requestAPI<any>('ovh')
    .then(data => {
      const projects = (data.projects as [any]).map((project, id) => {
        return {
          id,
          projectId: project,
        }
      }) as [OvhProject];
      setProjects(projects);
    })
    .catch(reason => {
      console.error(
        `Error while accessing the jupyter server clouder extension.\n${reason}`
      );
    });
  }, []);
  return (
    <>
      <Box>
        <Table.Container>
          <Table.Title as="h2" id="ssh-keys">
            SSH Keys
          </Table.Title>
          <Table.Subtitle as="p" id="ssh-keys-subtitle">
            List of SSH keys.
          </Table.Subtitle>
          <DataTable
            aria-labelledby="ssh-keys"
            aria-describedby="ssh-keys-subtitle" 
            data={projects}
            columns={[
              {
                header: 'Id',
                field: 'projectId',
                renderCell: row => <Text>{row.projectId}</Text>
              },
            ]}
          />
        </Table.Container>
      </Box>
    </>
  )
}

export default KeysTab;
